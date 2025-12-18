# sec_ilp_snapshot_v3.py
# ------------------------------------------------------------
# Risolve l'assegnazione di 1 o 3 task (RICHIESTE) su Ek = {k} U neighbors[k]
# partendo da uno snapshot JSON della simulazione (100-200s).
#
# Requisiti:
#   pip install pulp
#   (opzionali) gurobipy | highspy
#
# Esempi:
#   Caso 1 (un task):
#       python sec_ilp_snapshot_v3.py --from-prof-files --sim-file Generated_datasets/simulation_dataset.json --time 150 --sen-id "STARLINK-4491" --use-task-ids 7 --tasks-file Generated_datasets/generated_tasks_seed13_dur300.json5 --mode single  --solver AUTO
#   Caso 2 (tre task):
#       python sec_ilp_snapshot_v3.py --from-prof-files --sim-file Generated_datasets/simulation_dataset.json --time 150 --sen-id "STARLINK-4491" --use-task-ids 9 15 62 --tasks-file Generated_datasets/generated_tasks_seed13_dur300.json5 --mode triple  --solver AUTO
#
#
#
#   python sec_experiments_runner.py --sim simulation_dataset.json --tasks generated_tasks_seed13_dur300.json5 --time 150 --out ./sec_experiments_out
#
# ------------------------------------------------------------

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import json
import sys
import re
from pathlib import Path
# import os

import pulp as pl


# -----------------------------
# Data model & parsing
# -----------------------------

@dataclass
class QueueTask:
    task_id: str
    d_cpu: float
    d_net: float
    D: float


@dataclass
class SENState:
    B: float
    B_max: float
    Rmax_norm: float
    cpu_queue: List[QueueTask]
    net_queue: List[QueueTask]
    in_service_cpu: Optional[QueueTask] = None
    in_service_net: Optional[QueueTask] = None
    net_bw_bps: Optional[float] = None



@dataclass
class Snapshot:
    time: float
    listening_dome: List[str]
    neighbors: Dict[str, List[str]]
    sen: Dict[str, SENState]
    requests: List[QueueTask]
    pre_R: Dict[str, Dict[str, float]]  # precomputed R_ri per task_id -> {sen_id: value}
    pre_E: Dict[str, Dict[str, float]]  # precomputed E_ri per task_id -> {sen_id: value}


def _json5_relaxed_load(path: str):
    """
    Legge file di configurazione o task scritti in formato JSON5, 
    anche se non perfettamente standard.
    """
    txt = Path(path).read_text(encoding="utf-8")
    # rimuovi commenti /* ... */ e // ...
    txt = re.sub(r"/\*.*?\*/", "", txt, flags=re.S)
    txt = re.sub(r"//.*?$", "", txt, flags=re.M)
    # quota chiavi non quotate (heuristic)
    txt = re.sub(r'(?m)(^|[\{,\[])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:',
                 r'\1 "\2":', txt)
    # rimuovi virgole finali
    txt = re.sub(r",\s*([}\]])", r"\1", txt)
    return json.loads(txt)


def _to_qt(x: dict) -> QueueTask:
    # Converte un dizionario (task) in un oggetto QueueTask
    return QueueTask(
        task_id=str(x["task_id"]),
        d_cpu=float(x.get("exec_time", 0.0)),      # <-- usa exec_time
        d_net=float(x.get("transfer_time", 0.0)),  # <-- usa transfer_time
        D=float(x.get("deadline", 300.0)),         # <-- usa un default se non c'è
    )


def load_snapshot(path: str) -> Snapshot:
    # Carica uno snapshot della simulazione da file JSON - Prepara i dati di input per l’ILP
    
    with open(path, "r", encoding="utf-8") as f:
        j = json.load(f)

    sen: Dict[str, SENState] = {}
    for sid, s in j["sen"].items():
        q = s.get("queues", {})
        sen[sid] = SENState(
            B=float(s["B"]),
            B_max=float(s["B_max"]),
            Rmax_norm=float(s.get("Rmax_norm", 1.0)),
            cpu_queue=[_to_qt(t) for t in q.get("cpu", [])],
            net_queue=[_to_qt(t) for t in q.get("net", [])],
            in_service_cpu=(_to_qt(q["in_service_cpu"]) if "in_service_cpu" in q else None),
            in_service_net=(_to_qt(q["in_service_net"]) if "in_service_net" in q else None),
            net_bw_bps=float(s.get("net_bw_bps", 0)) or None,
        )

    pre_R = j.get("precomputed", {}).get("R_ri", {})
    pre_E = j.get("precomputed", {}).get("E_ri", {})
    reqs = [_to_qt(r) for r in j.get("requests", [])]

    return Snapshot(
        time=float(j["time"]),
        listening_dome=[str(x) for x in j.get("listening_dome", [])],
        neighbors={k: [str(v) for v in vals] for k, vals in j.get("neighbors", {}).items()},
        sen=sen,
        requests=reqs,
        pre_R=pre_R,
        pre_E=pre_E,
    )
    

def _qt_from_prof(item: dict) -> QueueTask:
    # Converte un task dal formato “prof” (simulazione del professore) in un QueueTask.
    
    d_cpu = item.get("exec_time", item.get("d_cpu", 0.0))

    # 1) se c'è già il transfer_time (in secondi), usa quello
    if "transfer_time" in item and item["transfer_time"] is not None:
        d_net = float(item["transfer_time"])  # secondi

    # 2) altrimenti, se c'è image_size (in MB), usalo come d_net (in MB)
    elif "image_size" in item and item["image_size"] is not None:
        d_net = float(item["image_size"])  # MB


    # 3) fallback: usa d_net così com’è (può essere bytes o s, a seconda dell’origine)
    else:
        d_net = float(item.get("d_net", 0.0))

    D = float(item.get("deadline", 300.0))
    tid = str(item.get("task_id", f"auto_{id(item)}"))
    return QueueTask(
        task_id=tid,
        d_cpu=float(d_cpu),
        d_net=float(d_net),
        D=D,
    )


def _build_neighbors_map_from_prof(sats_block: list) -> Dict[str, List[str]]:
    # Costruisce una mappa dei vicini per ogni satellite dal blocco dei satelliti.
    neigh = {}
    for s in sats_block:
        sid = str(s["satellite"])
        neigh[sid] = [str(n) for n in s.get("neighbors", [])]
    return neigh

def _build_sen_map_from_prof(sats_block: list) -> Dict[str, 'SENState']:
    # Costruisce la mappa degli stati dei satelliti (SENState) dal blocco dei satelliti.
    sen = {}
    for s in sats_block:
        sid = str(s["satellite"])
        # code del prof: 'energy_budget' = B disponibile
        B = float(s.get("energy_budget", 0.0))
        # non c'è B_max → usa B come proxy (oppure un default alto se vuoi normalizzazione stabile)
        B_max = B if B > 0 else 1.0
        qcpu = [_qt_from_prof(t) for t in s.get("queue_cpu", [])]
        qnet = [_qt_from_prof(t) for t in s.get("queue_net", [])]
        sen[sid] = SENState(
            B=B,
            B_max=B_max,
            Rmax_norm=1.0,           # non fornito dal prof
            cpu_queue=qcpu,
            net_queue=qnet,
            in_service_cpu=None,     # non fornito dal prof
            in_service_net=None,     # non fornito dal prof
            net_bw_bps=None          # non fornito → gestito dal default_net_bw_bps
        )
    return sen

# ------------ aggiunto 2025-10-15 ------------
# Calcola i coefficienti alpha_cpu, alpha_net a partire da parametri fisici
def alpha_from_physics(C_cpu: float, e_j_per_hz3: float, P_net_watt: float, bw_bps: float):
    alpha_cpu = e_j_per_hz3 * (C_cpu ** 3)  # J/s * s = J, ma qui d_cpu è in secondi → coeff lineare
    alpha_net = P_net_watt / max(bw_bps, 1e-9)  # J = W * (bytes / bytes/s)
    return alpha_cpu, alpha_net


def load_snapshot_from_prof_files(simulation_json: str,
                                  time_t: int,
                                  sen_k: str,
                                  tasks_json5: str | None = None,
                                  picked_task_ids: List[str] | None = None) -> 'Snapshot':
    """
    Costruisce lo Snapshot atteso dall'ILP partendo dai file del prof.
    - simulation_json: path a simulation_dataset.json
    - time_t: istante (intero) da cui estrarre lo stato
    - sen_k: satellite centrale k (es. 'STARLINK-5736')
    - tasks_json5: opzionale, path a generated_tasks_seed13_dur300.json5 (per richieste nuove)
    - picked_task_ids: opzionale, lista di task_id da trattare come 'requests' (override)
    """
    j = json.load(open(simulation_json, "r", encoding="utf-8"))

    # Trova il blocco al tempo richiesto
    block = None
    for b in j:
        if int(b.get("time")) == int(time_t):
            block = b
            break
    if not block:
        raise ValueError(f"Nel file {simulation_json} non esiste un blocco time={time_t}.")

    sats = block.get("satellites", [])
    neighbors = _build_neighbors_map_from_prof(sats)
    sen_map = _build_sen_map_from_prof(sats)

    # listening dome = tutti i satelliti con in_listening_dome == True
    listening = [str(s["satellite"]) for s in sats if s.get("in_listening_dome", False)]

    # requests (nuovi task da assegnare):
    # 1) Se picked_task_ids è fornito → usa quelli (cercandoli prima nel file tasks_json5, poi nelle code di k)
    # 2) Altrimenti, se tasks_json5 è fornito → carica qui i task “nuovi” (es. 1 o 3) e usali come richieste
    # 3) Altrimenti, fallback: prendi 1 task “sintetico” minimal (es. il primo della coda di k) per test
    requests: List[QueueTask] = []

    def _find_in_k_queue(task_id: str) -> QueueTask | None:
        kblock = next((s for s in sats if str(s["satellite"]) == str(sen_k)), None)
        if not kblock:
            return None
        allq = kblock.get("queue_cpu", []) + kblock.get("queue_net", [])
        for t in allq:
            if str(t["task_id"]) == str(task_id):
                return _qt_from_prof(t)
        return None

    tasks_source = []
    if tasks_json5:
        try:
            tj = _json5_relaxed_load(tasks_json5)
            # supponiamo struttura del tipo [{'task_id':..., 'd_cpu':..., 'deadline':..., 'd_net':...?, 'arrival':...}, ...]
            if isinstance(tj, dict) and "tasks" in tj:
                tasks_source = tj["tasks"]
            elif isinstance(tj, list):
                tasks_source = tj
        except Exception:
            # se il file non è leggibile, tasks_source resta vuoto e si va a fallback
            tasks_source = []

    if picked_task_ids:
        for rid in picked_task_ids:
            # priorità: tasks_json5
            hit = next((t for t in tasks_source if str(t.get("task_id")) == str(rid)), None)
            if hit:
                requests.append(_qt_from_prof(hit))
            else:
                # fallback: cerca nella coda di k
                hit2 = _find_in_k_queue(rid)
                if hit2:
                    requests.append(hit2)
    elif tasks_source:
        # prende tutti i task definiti nel file tasks_json5
        requests = [_qt_from_prof(t) for t in tasks_source]
    else:
        # fallback "sintetico": primo in coda su k se c’è
        kblock = next((s for s in sats if str(s["satellite"]) == str(sen_k)), None)
        if kblock and kblock.get("queue_cpu"):
            requests = [_qt_from_prof(kblock["queue_cpu"][0])]
        else:
            requests = []

    # pre_R / pre_E non presenti nei file del prof
    return Snapshot(
        time=float(time_t),
        listening_dome=listening,
        neighbors=neighbors,
        sen=sen_map,
        requests=requests,
        pre_R={},
        pre_E={}
    )


# -----------------------------
# Metrics: Wcpu, Wnet, R_ri, E_ri
# -----------------------------

def waiting_time_cpu(s: SENState) -> float:
    w = sum(t.d_cpu for t in s.cpu_queue)
    if s.in_service_cpu:
        w += 0.5 * s.in_service_cpu.d_cpu
    return w


def waiting_time_net(
    s: SENState,
    default_bw_MBps: float,
    queues_dnet_is_seconds: bool
) -> float:
    """
    Restituisce il tempo di attesa in coda rete (secondi).
    - Se queues_dnet_is_seconds=True, somma direttamente i secondi.
    - Altrimenti interpreta d_net come byte e divide per la banda (byte/s).
    """
    w = sum(t.d_net for t in s.net_queue)
    if s.in_service_net:
        w += 0.5 * s.in_service_net.d_net

    if queues_dnet_is_seconds:
        return float(w)  # qui w è già in secondi

    bw = (getattr(s, "net_bw_MBps", None) or default_bw_MBps)
    bw = max(bw, 1e-9)
    return float(w) / bw   # (MB) / (MB/s) = s


def compute_R_ri(task: QueueTask, s: SENState, pre_R_for_task: Optional[float],
                 default_net_bw_MBps: float, task_dnet_is_seconds: bool = False,
                 queues_dnet_is_seconds: bool = False) -> float:
    if pre_R_for_task is not None and pre_R_for_task > 0:
        return float(pre_R_for_task)

    if task_dnet_is_seconds:
        tx_time = float(task.d_net)  # già secondi
    else:
        bw = (getattr(s, "net_bw_MBps", None) or default_net_bw_MBps)
        bw = max(bw, 1e-9)
        tx_time = float(task.d_net) / bw  # MB / (MB/s) = s

    return (
        waiting_time_cpu(s)
        + waiting_time_net(s, default_net_bw_MBps, queues_dnet_is_seconds)
        + task.d_cpu
        + tx_time
    )




def compute_E_ri(task: QueueTask,
                 pre_E_for_task: Optional[float],
                 alpha_cpu: float,
                 alpha_net: float) -> float:
    """
    Se pre_E_for_task è fornito (>0), usa quello.
    Altrimenti stima lineare: eps_cpu = alpha_cpu * d_cpu, eps_net = alpha_net * d_net
    """
    if pre_E_for_task is not None and pre_E_for_task > 0:
        return float(pre_E_for_task)
    return alpha_cpu * task.d_cpu + alpha_net * task.d_net


# -----------------------------
# ILP su E_k
# -----------------------------

def pick_solver(solver_name: str = "AUTO"):
    """
    Restituisce un solver PuLP in base alla preferenza.
    - AUTO: prova GUROBI -> HiGHS (highspy) -> CBC
    - GUROBI/HIGHS/CBC: forzati
    """
    name = (solver_name or "AUTO").upper()
    if name == "GUROBI":
        try:
            return pl.GUROBI(msg=False, mipgap=0.01, timeLimit=60)
        except Exception as e:
            print("[WARN] GUROBI non disponibile:", e)
            raise
    elif name == "HIGHS":
        try:
            return pl.HiGHS(msg=False)  # richiede `pip install highspy`
        except Exception as e:
            print("[WARN] HiGHS non disponibile:", e)
            raise
    elif name == "CBC":
        return pl.PULP_CBC_CMD(msg=False, timeLimit=60)
    else:
        # AUTO
        try:
            return pl.GUROBI(msg=False, mipgap=0.01, timeLimit=60)
        except Exception:
            try:
                return pl.HiGHS(msg=False)  
            except Exception:
                return pl.PULP_CBC_CMD(msg=False, timeLimit=60)


def solve_on_Ek(snapshot: Snapshot, k: str, picked_tasks: List[str],
                w_energy: float = 0.5, w_time: float = 0.5,
                alpha_cpu: float = 1.0, alpha_net: float = 5000.0,
                solver_name: str = "AUTO", use_node_Rmax_norm: bool = False,
                default_net_bw_MBps: float = 3125.0,
                debug: bool = False,
                tasks_from_prof: bool = False) -> dict:

    """
    Costruisce e risolve l'ILP su E_k (k + neighbors[k]) per i task in picked_tasks.
    - use_node_Rmax_norm=False: normalizza R rispetto al max R calcolato per nodo.
    - use_node_Rmax_norm=True: usa Rmax_norm del nodo (se >0), utile per coerenza fra snapshot diversi.
    """

    if k not in snapshot.sen:
        raise ValueError(f"Il SEN '{k}' non esiste nello snapshot.")

    Ek = [k] + snapshot.neighbors.get(k, [])
    Ek = [sid for sid in Ek if sid in snapshot.sen]  # filtra eventuali id assenti

    if not Ek:
        raise ValueError(f"Nessun nodo in Ek per '{k}'. Controlla neighbors nel JSON.")

    tasks_map: Dict[str, QueueTask] = {t.task_id: t for t in snapshot.requests}
    missing = [tid for tid in picked_tasks if tid not in tasks_map]
    if missing:
        raise ValueError(f"Task non trovati nello snapshot: {missing}")

    tasks = {tid: tasks_map[tid] for tid in picked_tasks}

    # Pre-calcolo R,E per ogni (r,i)
    R: Dict[str, Dict[str, float]] = {}
    E: Dict[str, Dict[str, float]] = {}
    for r_id, t in tasks.items():
        R[r_id] = {}
        E[r_id] = {}
        for i in Ek:
            s = snapshot.sen[i]
            preR = snapshot.pre_R.get(r_id, {}).get(i)
            preE = snapshot.pre_E.get(r_id, {}).get(i)
            R[r_id][i] = compute_R_ri(
                t, s, preR,
                default_net_bw_MBps=default_net_bw_MBps,
                task_dnet_is_seconds=False,
                queues_dnet_is_seconds=False
            )
            E[r_id][i] = compute_E_ri(t, preE, alpha_cpu, alpha_net)



    # Normalizzazione R     ------ modificata 2025-10-15 ------
    # (Ora normalizziamo R per-task)
    Rnorm_ref = {r: max(1e-9, max(R[r][i] for i in Ek)) for r in tasks}

    # Costruzione ILP
    prob = pl.LpProblem("SEC_Snapshot_Assignment", pl.LpMinimize)
    x = pl.LpVariable.dicts("x", (list(tasks.keys()), Ek), lowBound=0, upBound=1, cat=pl.LpBinary)

    # Obiettivo: somma pesata e normalizzata (energia + tempo)
    objective_terms = []
    
    # --- traccia delle componenti normalizzate per ricostruire f fuori dal solver ---
    obj_terms_E = {}  # (r,i) -> E_normalizzato = E[r][i] / B_max[i]
    obj_terms_R = {}  # (r,i) -> R_normalizzato = R[r][i] / Rnorm_ref[r]

    for r in tasks:
        for i in Ek:
            Enorm = E[r][i] / max(snapshot.sen[i].B_max, 1e-9)
            Rnormed = R[r][i] / Rnorm_ref[r]   # <-- per-task

            # salva le componenti
            obj_terms_E[(r, i)] = Enorm
            obj_terms_R[(r, i)] = Rnormed

            objective_terms.append(x[r][i] * (w_energy * Enorm + w_time * Rnormed))

    prob += pl.lpSum(objective_terms), "WeightedNormalizedEnergyTime"

    # Vincoli: assegnazione unica
    for r in tasks:
        prob += pl.lpSum(x[r][i] for i in Ek) == 1, f"assign_once_{r}"

    # Vincoli: budget energetico per nodo
    for i in Ek:
        prob += pl.lpSum(x[r][i] * E[r][i] for r in tasks) <= snapshot.sen[i].B, f"energy_budget_{i}"

    # Vincoli: deadline con big-M
    M = 10 ** 6
    for r, t in tasks.items():
        for i in Ek:
            prob += R[r][i] <= t.D + M * (1 - x[r][i]), f"deadline_{r}_{i}"

    # Risoluzione
    solver = pick_solver(solver_name)
    prob.solve(solver)

    status = pl.LpStatus[prob.status]
    obj = pl.value(prob.objective)
    
    obj_Esum = 0.0
    obj_Rsum = 0.0
    for r in tasks:
        for i in Ek:
            val = pl.value(x[r][i])
            if val is not None and val > 0.5:
                obj_Esum += obj_terms_E[(r, i)]
                obj_Rsum += obj_terms_R[(r, i)]

    assignments = []
    for r in tasks:
        for i in Ek:
            val = pl.value(x[r][i])
            if val is not None and val > 0.5:
                assignments.append({
                    "task": r,
                    "sen": i,
                    "R": float(R[r][i]),
                    "E": float(E[r][i]),
                    "Rnormed": float(obj_terms_R[(r, i)]),
                    "Enorm": float(obj_terms_E[(r, i)]),
                })

    return {
        "status": status,
        "objective": float(obj) if obj is not None else None,
        "obj_Esum": float(obj_Esum),
        "obj_Rsum": float(obj_Rsum),
        "assignments": assignments,
        "solver": solver.__class__.__name__,
        "Ek": Ek
    }


# ----------------------------- approccio ε-constraint/gerarchico
def solve_on_Ek_hierarchical(
    snapshot: Snapshot,
    k: str,
    picked_tasks: List[str],
    primary: str = "energy",        # "energy" oppure "time"
    tol: float = 0.10,               # tolleranza in [0,1], es. 0.10 = +10%
    alpha_cpu: float = 1.0,
    alpha_net: float = 5000.0,
    solver_name: str = "AUTO",
    default_net_bw_MBps: float = 3125.0,
    debug: bool = False,
    tasks_from_prof: bool = False
) -> dict:
    """
    Approccio gerarchico (ε-constraint):
    - Se primary="energy": 
        1) min sum E (fase A)  
        2) min sum R con vincolo sum E <= (1+tol)*E_opt (fase B)
    - Se primary="time": 
        1) min sum R (fase A)
        2) min sum E con vincolo sum R <= (1+tol)*R_opt (fase B)
    Tutti i vincoli originali restano attivi in entrambe le fasi.
    """
    # ---- Prepara Ek e (R,E) identico a solve_on_Ek ---------------------------------
    if k not in snapshot.sen:
        raise ValueError(f"Il SEN '{k}' non esiste nello snapshot.")

    Ek = [k] + snapshot.neighbors.get(k, [])
    Ek = [sid for sid in Ek if sid in snapshot.sen]
    if not Ek:
        raise ValueError(f"Nessun nodo in Ek per '{k}'. Controlla neighbors nel JSON.")

    tasks_map: Dict[str, QueueTask] = {t.task_id: t for t in snapshot.requests}
    missing = [tid for tid in picked_tasks if tid not in tasks_map]
    if missing:
        raise ValueError(f"Task non trovati nello snapshot: {missing}")
    tasks = {tid: tasks_map[tid] for tid in picked_tasks}

    R: Dict[str, Dict[str, float]] = {}
    E: Dict[str, Dict[str, float]] = {}
    for r_id, t in tasks.items():
        R[r_id] = {}
        E[r_id] = {}
        for i in Ek:
            s = snapshot.sen[i]
            preR = snapshot.pre_R.get(r_id, {}).get(i)
            preE = snapshot.pre_E.get(r_id, {}).get(i)
            R[r_id][i] = compute_R_ri(
                t, s, preR,
                default_net_bw_MBps=default_net_bw_MBps,
                task_dnet_is_seconds=False,
                queues_dnet_is_seconds=False
            )
            E[r_id][i] = compute_E_ri(t, preE, alpha_cpu, alpha_net)

    # Utility per costruire un problema con vincoli "di base"
    def _build_base_lp(minimize: str):
        prob = pl.LpProblem("SEC_Hierarchical", pl.LpMinimize)
        x = pl.LpVariable.dicts("x", (list(tasks.keys()), Ek), lowBound=0, upBound=1, cat=pl.LpBinary)

        # Obiettivo primario semplice (NON normalizzato): sum E oppure sum R
        if minimize == "energy":
            prob += pl.lpSum(x[r][i] * E[r][i] for r in tasks for i in Ek), "MinEnergy"
        elif minimize == "time":
            prob += pl.lpSum(x[r][i] * R[r][i] for r in tasks for i in Ek), "MinTime"
        else:
            raise ValueError("minimize deve essere 'energy' o 'time'")

        # Vincoli: assegnazione unica
        for r in tasks:
            prob += pl.lpSum(x[r][i] for i in Ek) == 1, f"assign_once_{r}"

        # Vincoli: budget energetico per nodo
        for i in Ek:
            prob += pl.lpSum(x[r][i] * E[r][i] for r in tasks) <= snapshot.sen[i].B, f"energy_budget_{i}"

        # Vincoli: deadline con big-M
        M = 10 ** 6
        for r, t in tasks.items():
            for i in Ek:
                prob += R[r][i] <= t.D + M * (1 - x[r][i]), f"deadline_{r}_{i}"

        return prob, x

    # ------------------- Fase A: ottimo primario -------------------
    primary = (primary or "energy").lower()
    if primary not in ("energy", "time"):
        raise ValueError("primary deve essere 'energy' o 'time'")

    probA, xA = _build_base_lp("energy" if primary == "energy" else "time")
    solver = pick_solver(solver_name)
    probA.solve(solver)
    statusA = pl.LpStatus[probA.status]

    # valore ottimo primario (se disponibile)
    optA_val = float(pl.value(probA.objective)) if pl.value(probA.objective) is not None else None

    if statusA != "Optimal":
        # ritorna subito se non ottimo (o infeasible)
        return {
            "stage": "A",
            "status": statusA,
            "primary": primary,
            "tol": tol,
            "opt_primary": optA_val,
            "objective_primary": optA_val,
            "objective": optA_val,  # alias per compatibilità col main
            "assignments": [],
            "Ek": Ek,
            "solver": solver.__class__.__name__,
            "note": "Prima fase non ottimale; impossibile procedere alla fase B."
        }

    # valore ottimo primario
    opt_primary = optA_val

    # ------------------- Fase B: ottimo secondario con vincolo ε -------------------
    # ricostruisci base LP con obiettivo secondario
    secondary = "time" if primary == "energy" else "energy"
    probB, xB = _build_base_lp(secondary)

    # aggiungi vincolo ε-constraint
    if primary == "energy":
        # sum E <= (1 + tol) * E_opt
        probB += pl.lpSum(xB[r][i] * E[r][i] for r in tasks for i in Ek) <= (1.0 + tol) * opt_primary, "epsilon_energy"
    else:
        # sum R <= (1 + tol) * R_opt
        probB += pl.lpSum(xB[r][i] * R[r][i] for r in tasks for i in Ek) <= (1.0 + tol) * opt_primary, "epsilon_time"

    probB.solve(solver)
    statusB = pl.LpStatus[probB.status]
    objB_val = float(pl.value(probB.objective)) if pl.value(probB.objective) is not None else None

    # DEBUG -------------
    sumE_B = sum(
        E[r][i] for r in tasks for i in Ek
        if (pl.value(xB[r][i]) is not None and pl.value(xB[r][i]) > 0.5)
    )
    sumR_B = sum(
        R[r][i] for r in tasks for i in Ek
        if (pl.value(xB[r][i]) is not None and pl.value(xB[r][i]) > 0.5)
    )

    if primary == "energy":
        rhs = (1.0 + tol) * opt_primary  # opt_primary = E_opt
        active = abs(sumE_B - rhs) < 1e-6 or sumE_B > 0.999999 * rhs
        print(f"[ε-constraint energy] sumE_B={sumE_B:.6f}  bound={rhs:.6f}  active={active}")
    else:  # primary == "time"
        rhs = (1.0 + tol) * opt_primary  # opt_primary = R_opt
        active = abs(sumR_B - rhs) < 1e-6 or sumR_B > 0.999999 * rhs
        print(f"[ε-constraint time]   sumR_B={sumR_B:.6f}  bound={rhs:.6f}  active={active}")
    # -------------------
        

    # Estrai assegnazioni e metriche dalla fase B (se fattibile), altrimenti dalla fase A
    use_prob, use_x, use_status = (probB, xB, statusB) if statusB in ("Optimal", "Feasible") else (probA, xA, statusA)
    assignments = []
    sumE = 0.0
    sumR = 0.0
    for r in tasks:
        for i in Ek:
            val = pl.value(use_x[r][i])
            if val is not None and val > 0.5:
                assignments.append({
                    "task": r,
                    "sen": i,
                    "R": float(R[r][i]),
                    "E": float(E[r][i]),
                })
                sumE += float(E[r][i])
                sumR += float(R[r][i])

    # costruisci risultato + alias compatibilità
    result = {
        "stage": "B" if use_prob is probB else "A",
        "status": use_status,
        "primary": primary,
        "tol": tol,
        "opt_primary": opt_primary,
        "objective_secondary": objB_val if use_prob is probB else None,
        "sumE": float(sumE),
        "sumR": float(sumR),
        "assignments": assignments,
        "Ek": Ek,
        "solver": solver.__class__.__name__,
    }
    # alias 'objective' per compatibilità con main:
    # - se abbiamo usato B: objective = objective_secondary
    # - se siamo rimasti in A: objective = opt_primary
    result["objective"] = result["objective_secondary"] if use_prob is probB else opt_primary
    return result



# -----------------------------
# CLI interface
# -----------------------------

def main():
    import argparse

    ap = argparse.ArgumentParser(description="Assegnazione ILP su snapshot SEC (Ek = k + neighbors[k]).")
    ap.add_argument("--snapshot", required=False, help="Percorso al file JSON dello snapshot (es: snapshot_150s.json)")
    ap.add_argument("--sen-id", required=True, help="Identificativo del SEN k su cui costruire Ek")
    ap.add_argument("--mode", choices=["single", "triple"], default="single",
                    help="single: 1 task; triple: 3 task dalle RICHIESTE")
    ap.add_argument("--tasks", nargs="*", help="Lista task_id da usare (override di mode).")
    ap.add_argument("--solver", choices=["AUTO", "GUROBI", "HIGHS", "CBC"], default="AUTO",
                    help="Selezione solver")
    ap.add_argument("--w-energy", type=float, default=0.5, help="Peso energia in obiettivo [0..1]")
    ap.add_argument("--w-time", type=float, default=0.5, help="Peso tempo in obiettivo [0..1]")
    ap.add_argument("--alpha-cpu", type=float, default=1.0, help="Coeff. per stima energia CPU (se E non fornita)")
    ap.add_argument("--alpha-net", type=float, default=5000.0, help="Coeff. per stima energia NET (se E non fornita)")
    ap.add_argument("--use-node-Rmax-norm", action="store_true",
                    help="Usa Rmax_norm del nodo per la normalizzazione dei tempi")
    ap.add_argument("--net-bw", type=float, default=3125.0,
        help="Banda di rete di default in MB/s (es. 3125 = ~25 Gbps)")
    ap.add_argument("--debug", action="store_true",
                help="Stampa tabella di fattibilità (R<=D, E<=B) per ogni (task, node)")
    ap.add_argument("--from-prof-files", action="store_true",
                    help="Leggi i dati dal formato del prof (simulation_dataset.json + opzionale generated_tasks*.json5)")
    ap.add_argument("--sim-file", help="Path a simulation_dataset.json")
    ap.add_argument("--time", type=int, help="Tempo della simulazione da usare (es. 150)")
    ap.add_argument("--tasks-file", help="Path a generated_tasks_seed13_dur300.json5 (opzionale)")
    ap.add_argument("--use-task-ids", nargs="*", help="Se specificati, usa questi task_id come richieste")
    ap.add_argument("--auto-alpha", action="store_true",
                    help="Calcola alpha_cpu e alpha_net da parametri fisici (paper).")
    ap.add_argument("--C-cpu", type=float, default=1e7,
                    help="C_CPU (Hz) per il calcolo alpha_cpu se --auto-alpha.")
    ap.add_argument("--e", type=float, default=5e-26,
                    help="Costante e (J/Hz^3) per alpha_cpu se --auto-alpha.")
    ap.add_argument("--P-net", type=float, default=1.0,
                    help="Potenza di rete (W) per alpha_net se --auto-alpha.")
    ap.add_argument("--lexi", choices=["off","energy","time"], default="off",
                help="Approccio gerarchico: off=disabilitato; energy=time secondario; time=energy secondario")
    ap.add_argument("--tol", type=float, default=0.10, help="Tolleranza gerarchica (0..1)")



    args = ap.parse_args()

    # Carica snapshot
    # snap = load_snapshot(args.snapshot)
    
    if args.from_prof_files:
        if not args.sim_file or args.time is None or not args.sen_id:
            print("Per --from-prof-files servono --sim-file, --time e --sen-id.", file=sys.stderr)
            sys.exit(2)
        snap = load_snapshot_from_prof_files(
            simulation_json=args.sim_file,
            time_t=args.time,
            sen_k=args.sen_id,
            tasks_json5=args.tasks_file,
            picked_task_ids=args.use_task_ids
        )
    else:
        if not args.snapshot:
            print("Specifica --snapshot oppure usa --from-prof-files.", file=sys.stderr)
            sys.exit(2)
        snap = load_snapshot(args.snapshot)

    
    # Se non specificati i task, prendi i primi 1 o 3
    if args.tasks and len(args.tasks) > 0:
        picked = args.tasks
    else:
        n = 1 if args.mode == "single" else 3
        picked = [r.task_id for r in snap.requests][:n]

    if not picked:
        print("[ERRORE] Nessun task selezionato. Controlla --tasks o lo snapshot.requests.")
        sys.exit(1)

    try:
        tasks_from_prof_flag = bool(args.from_prof_files)

        # -------------- aggiunto 2025-10-15 --------------
        # Calcolo alpha secondo preferenza
        if args.auto_alpha:
            calc_alpha_cpu, calc_alpha_net = alpha_from_physics(
                C_cpu=args.C_cpu,
                e_j_per_hz3=args.e,
                P_net_watt=args.P_net,
                bw_bps=args.net_bw
            )
            alpha_cpu = calc_alpha_cpu
            alpha_net = calc_alpha_net if not bool(args.from_prof_files) else args.P_net
            # Nota: se i file del prof hanno d_net in secondi (tasks_from_prof=True),
            # allora epsilon_net = P_net * d_net -> usa alpha_net = P_net.
        
        else:
            alpha_cpu = args.alpha_cpu
            alpha_net = args.alpha_net

        if args.lexi != "off":
            res = solve_on_Ek_hierarchical(
                snapshot=snap,
                k=args.sen_id,
                picked_tasks=picked,
                primary=args.lexi,
                tol=args.tol,
                alpha_cpu=alpha_cpu,
                alpha_net=alpha_net,
                solver_name=args.solver,
                default_net_bw_MBps=args.net_bw,
                debug=args.debug,
                tasks_from_prof=tasks_from_prof_flag
            )
        else:
            res = solve_on_Ek(
                snapshot=snap,
                k=args.sen_id,
                picked_tasks=picked,
                w_energy=args.w_energy,
                w_time=args.w_time,
                alpha_cpu=alpha_cpu,
                alpha_net=alpha_net,
                solver_name=args.solver,
                use_node_Rmax_norm=args.use_node_Rmax_norm,
                default_net_bw_MBps=args.net_bw,
                debug=args.debug,
                tasks_from_prof=tasks_from_prof_flag
            )


    except Exception as e:
        print("[ERRORE] Risoluzione fallita:", e)
        sys.exit(2)

    # Output sintetico a console
    print(f"Time snapshot: {snap.time}")
    print(f"SEN k: {args.sen_id} | Ek: {res['Ek']}")
    print(f"Solver: {res['solver']}")
    print("Status:", res.get("status"))
    print("Objective:", res.get("objective"))  # compatibile con pesato e gerarchico

    # Extra info per gerarchico
    if args.lexi != "off":
        print(f"[Hierarchical] primary={res.get('primary')} tol={res.get('tol')}")
        if res.get("opt_primary") is not None:
            print(f"  Opt(primary): {res.get('opt_primary')}")
        if res.get("objective_secondary") is not None:
            print(f"  Objective(secondary): {res.get('objective_secondary')}")
        if (res.get("sumE") is not None) and (res.get("sumR") is not None):
            print(f"  Totals -> E: {res.get('sumE')}  R: {res.get('sumR')}")

    print("Assignments:")
    if not res.get("assignments"):
        print("  [nessuna assegnazione trovata]")
    else:
        for a in res["assignments"]:
            # campi base
            line = f"  - {a['task']} -> {a['sen']} | R={a['R']:.3f} | E={a['E']:.6f}"
            # campi opzionali (solo obiettivo pesato)
            if "Rnormed" in a:
                line += f" | Rnorm={a['Rnormed']:.6f}"
            if "Enorm" in a:
                line += f" | Enorm={a['Enorm']:.6f}"
            print(line)

if __name__ == "__main__":
    main()


"""

python sec_ilp_snapshot_v3.py --from-prof-files --sim-file simulation_dataset.json --time 150 --sen-id "STARLINK-4491" --tasks-file generated_tasks_seed13_dur300.json5 --mode single --use-task-ids 5 --solver AUTO --net-bw 3125 --w-energy 0.5 --w-time 0.5


python sec_ilp_snapshot_v3.py --from-prof-files --sim-file simulation_dataset.json --time 150 --sen-id "STARLINK-4491" --tasks-file generated_tasks_seed13_dur300.json5 --use-task-ids 84 9 45 --mode triple --solver AUTO --net-bw 3125 --w-energy 0.5 --w-time 0.5


python sec_ilp_snapshot_v3.py --from-prof-files --sim-file simulation_dataset.json --time 150 --sen-id "STARLINK-4491" --tasks-file generated_tasks_seed13_dur300.json5 --mode single --lexi energy --tol 0.10 --use-task-ids 5 --solver AUTO --net-bw 3125


"""