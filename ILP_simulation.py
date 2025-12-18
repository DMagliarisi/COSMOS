import globals
from Task import Task
from sec_ilp_snapshot_v3 import solve_on_Ek, Snapshot, SENState, QueueTask, alpha_from_physics, solve_on_Ek_hierarchical

config = globals.config
resolution_config = globals.resolution_config

c_sen_config_global = config.get("C_sen", 1e9)
if isinstance(c_sen_config_global, dict):
    c_min = c_sen_config_global.get("min", 1e9)
    c_max = c_sen_config_global.get("max", 1e9)
    C_sen = globals.rnd.uniform(c_min, c_max)
else:
    C_sen = float(c_sen_config_global)

bw_MBps = float(config.get("available_bandwidth", {}).get("min", 2150.0))
bw_Bps = bw_MBps * (1024 ** 2) if bw_MBps is not None else 0.0
e_coeff = config.get("energy_coefficient", 5e-26)  # coefficiente energetico (esempio numerico)


def network_metrics(image_size_MB, Volume_size_MB=0.0):
    """
    Calcola la larghezza di banda disponibile (in Bps) e la dimensione totale dei dati (in Byte).

    Args:
        image_size_MB (float): Dimensione dell'immagine in MB.
        Volume_size_MB (float, optional): Dimensione aggiuntiva del volume in MB. Default a 0.0.

    Returns:
        tuple: (bw_Bps, data_bytes)
    """

    # Calcola la dimensione totale dei dati in MB e poi in Byte
    total_data_MB = image_size_MB + Volume_size_MB
    data_bytes = total_data_MB * (1024 ** 2)

    return bw_Bps, data_bytes


def TaskAssignment_ILP(env, selected_server, task_id, image_size,
                   arrival_time_system, num_hops, transfer_time,
                   task_type, d_cpu, D_r, net_bw_override_Bps=None,
                   result_sink=None, allow_retry=False, routing_already_charged=False, routing_energy_from=None):
    """
    Processo SimPy che assegna un task al server selezionato e simula:
      - attesa nella coda CPU (cpu_dev)
      - esecuzione CPU (yield timeout)
      - eventuale trasferimento su coda NET (net_dev)
      - downlink verso Ground Unit per alcuni task (delay_to_transfer)

    Parametri principali:
      - env: ambiente SimPy
      - selected_server: oggetto server che espone metodi e risorse (W_cpu, W_net, cpu_dev, net_dev, ecc.)
      - image_size: dimensione immagine in MB
      - transfer_time: tempo di trasferimento dal nodo sorgente a questo server (già calcolato)
    """
    if result_sink is None:
        result_sink = {}
    result_sink["ok"] = False
    result_sink["reason"] = None
    result_sink["fatal"] = False

    # helper per rifiuto
    def reject(reason, fatal=False):
        # salva info nel sink
        result_sink["ok"] = False
        result_sink["reason"] = reason
        result_sink["fatal"] = fatal
        # se non stiamo facendo fallback, o il fallimento è fatale,
        # logghiamo davvero il rifiuto
        if (not allow_retry) or fatal:
            selected_server.record_rejected_task(task_id, task_type,
                                                 arrival_time_system, image_size, reason, d_cpu)

        return

    ENABLE_MONITORING = config.get("enable_queue_monitoring", False)

    task_OBS = Task(task_id, selected_server.name, 'OBS', env.now, task_type, image_size)
    bw_Bps, data_bytes = network_metrics(image_size)

    # Inizializza gli attributi solo se il monitoraggio è attivo
    if ENABLE_MONITORING:
        task_OBS.d_cpu = d_cpu
        task_OBS.d_net = data_bytes / bw_Bps if bw_Bps > 0 else 0.0
        task_OBS.deadline = arrival_time_system + D_r
        task_OBS.image_size_MB = image_size

    # aspetta il trasferimento iniziale verso il selected_server
    yield env.timeout(transfer_time)
    T_deadline_abs = arrival_time_system + D_r
    D_rem = T_deadline_abs - env.now
    if D_rem <= 0:
        reject("Deadline Exceeded", fatal=True)
        result_sink["ok"] = False
        return
    arrival_time_task_queue = env.now

    # helper per contare utenti + queue. calcola il numero totale di task associati a una risorsa in un preciso istante, sommando sia i task in servizio sia quelli in coda
    def _res_len_with_users(res):
        # numero di task che stanno attualmente utilizzando la risorsa (res.users)
        users_len = len(getattr(res, "users", []))
        # numero di task che sono attualmente in attesa nella coda (res.queue)
        queue_len = len(getattr(res, "queue", []))
        return users_len + queue_len

    # inizializza variabili di coda per reporting
    qlen_on_enqueue_cpu = None
    qlen_on_enqueue_net = None

    eps_cpu, eps_net, time_in_queue = 0.0, 0.0, 0.0  # energia stimata CPU / NET che useremo per riserve e sottrazioni
    start_time = env.now

    _, data_bytes = network_metrics(image_size)  # solo i bytes
    eff_Bps = net_bw_override_Bps if (net_bw_override_Bps and net_bw_override_Bps > 0) else bw_Bps

    d_net = data_bytes / eff_Bps if eff_Bps > 0 else 0.0
    eps_net = selected_server.compute_routing_energy(data_bytes, eff_Bps if eff_Bps > 0 else 1.0,
                                                     config.get("Ptrasm", 1.0))
    net_time = d_net

    # stime di attesa (metodi del server; se non esistono, fallback a 0). Wn e Wc sono latenze di attesa stimate nelle code (metodi del server)
    try:
        Wn = selected_server.W_net()
    except Exception:
        Wn = getattr(selected_server, "W_net", lambda: 0.0)()

    try:
        Wc = selected_server.W_cpu()
    except Exception:
        Wc = getattr(selected_server, "W_cpu", lambda: 0.0)()

    # ---------------------------
    # Branch: Generic / CPU_Intensive (solo CPU)
    # ---------------------------
    if task_type in ("Generic_Service", "CPU_Intensive"):
        # energia richiesta per eseguire il task sul server
        eps_cpu = selected_server.compute_execution_energy(d_cpu, C_sen, e=e_coeff)
        # controllo se il server ha energia disponibile (tenendo conto delle riserve)
        if selected_server.energy - selected_server.energy_reserved < eps_cpu:
            reject("Insufficient Energy for CPU", fatal=False)
        # controllo se il server ha energia disponibile (tenendo conto delle riserve)
        R = Wc + d_cpu + d_net
        if R > D_r:
            # se la stima supera la deadline configurata, rifiuta il task
            selected_server.record_rejected_task(task_id, task_type, arrival_time_system, image_size,
                                                 "Deadline Exceeded", d_cpu)

            result_sink["ok"] = False
            return
            # riservo energia per evitare race condition con altri task
        selected_server.energy_reserved += eps_cpu

        # richiedo la risorsa CPU (SimPy Resource) - il server tiene una coda interna
        req_cpu = selected_server.cpu_dev.request()
        qlen_on_enqueue_cpu = _res_len_with_users(selected_server.cpu_dev)
        if ENABLE_MONITORING:
            req_cpu.task_data = task_OBS
            print(
                f"[{env.now:.3f}] Task {task_id} enqueued on CPU {selected_server.name} qlen_enqueue={qlen_on_enqueue_cpu}")

        # riservo energia
        selected_server.energy_reserved += eps_cpu
        req_cpu = selected_server.cpu_dev.request()
        # attendi servizio CPU
        yield req_cpu
        # quando ottengo la CPU, registro il tempo passato in coda
        # Ora abbiamo la risorsa, ma l'energia esiste ancora?
        if selected_server.energy < eps_cpu:
            selected_server.energy_reserved -= eps_cpu  # Rilascio la prenotazione
            selected_server.cpu_dev.release(req_cpu)  # Rilascio la risorsa
            reject("Insufficient Energy for CPU", fatal=True)
            return
        time_in_queue = env.now - arrival_time_task_queue
        selected_server.cpu_busy_until = env.now + d_cpu

        # esecuzione CPU
        yield env.timeout(d_cpu)
        selected_server.cpu_busy_until = env.now  # reset quando il task finisce
        # sottraggo l'energia CPU effettivamente consumata
        selected_server.energy -= eps_cpu
        selected_server.cpu_dev.release(req_cpu)

        # -------------------------
        # Downlink verso Ground User: si assume che l'output abbia dimensione = image_size
        # -------------------------
        BANDWIDTH_TO_GU_BPS = config.get("Bandwidth_to_GU_Bps", 100000)  # ATTENZIONE: unità devono essere B/s
        file_size_bytes = image_size * (1024 ** 2)  # conversione MB -> byte
        delay_to_transfer = file_size_bytes / BANDWIDTH_TO_GU_BPS  # tempo di trasmissione verso GU

        # rilascio riserva dopo tutte le operazioni che dipendono dalla CPU
        yield env.timeout(delay_to_transfer)
        selected_server.energy_reserved -= eps_cpu

    # ---------------------------
    # Branch: CPU_and_Data_Intensive (CPU + NET)
    # ---------------------------
    elif task_type in ("CPU_and_Data_Intensive"):
        eps_cpu = selected_server.compute_execution_energy(d_cpu, C_sen, e=e_coeff)

        if selected_server.energy - selected_server.energy_reserved < (eps_cpu + eps_net):
            reject("Insufficient Energy for CPU+NET", fatal=False)
            return
        # Qui d_cpu e d_net sono i tempi di servizio per il task R
        R = Wc + d_cpu + Wn + d_net
        if R > D_r:
            selected_server.record_rejected_task(task_id, task_type, arrival_time_system, image_size,
                                                 "Deadline Exceeded", d_cpu)

            result_sink["ok"] = False
            return

        # riservo energia totale (CPU + NET)
        selected_server.energy_reserved += (eps_cpu + eps_net)

        # enqueue sulla CPU
        req_cpu = selected_server.cpu_dev.request()
        qlen_on_enqueue_cpu = _res_len_with_users(selected_server.cpu_dev)
        if ENABLE_MONITORING:
            req_cpu.task_data = task_OBS
        print(
            f"[{env.now:.3f}] Task {task_id} enqueued on CPU {selected_server.name} qlen_enqueue={qlen_on_enqueue_cpu}")

        # attendi la CPU
        yield req_cpu

        # === FIX: Check Energia CPU POST-Coda ===
        if selected_server.energy < eps_cpu:
            selected_server.energy_reserved -= (eps_cpu + eps_net)
            selected_server.cpu_dev.release(req_cpu)
            reject("Insufficient Energy for CPU", fatal=True)
            return
        # ========================================

        Wc = env.now - arrival_time_task_queue
        selected_server.cpu_busy_until = env.now + d_cpu

        # esecuzione CPU
        yield env.timeout(d_cpu)
        selected_server.cpu_busy_until = env.now
        selected_server.energy -= eps_cpu
        selected_server.cpu_dev.release(req_cpu)

        cpu_service_end = env.now

        # ora enqueue sulla NET (se richiesto)
        req_net = selected_server.net_dev.request()
        qlen_on_enqueue_net = _res_len_with_users(selected_server.net_dev)
        if ENABLE_MONITORING:
            req_net.task_data = task_OBS
        print(
            f"[{env.now:.3f}] Task {task_id} enqueued on NET {selected_server.name} qlen_enqueue_net={qlen_on_enqueue_net}")

        yield req_net

        # === FIX: Check Energia NET POST-Coda ===
        # Nota: l'energia CPU è già stata spesa. Controlliamo se c'è quella NET.
        if selected_server.energy < eps_net:
            selected_server.energy_reserved -= eps_net  # Rilascio il residuo prenotato
            selected_server.net_dev.release(req_net)
            # Tecnicamente il task è fallito a metà, lo registriamo come rejected o fail
            reject("Insufficient Energy for NET", fatal=True)
            return
        # ========================================

        Wn = env.now - cpu_service_end
        if Wn < 0:
            Wn = 0.0

        selected_server.net_busy_until = env.now + net_time

        yield env.timeout(net_time)
        selected_server.net_busy_until = env.now
        if not routing_already_charged:
            selected_server.energy -= eps_net
        else:
            # opzionale: se vuoi, registra che la routing-energy è stata già presa da `routing_energy_from`.
            pass
        selected_server.net_dev.release(req_net)

        time_in_queue = Wc + Wn
        selected_server.energy_reserved -= (eps_cpu + eps_net)

    # ---------------------------
    # Branch: Batch (solo NET)
    # ---------------------------
    elif task_type == "Batch":
        # Solo coda Network
        if selected_server.energy - selected_server.energy_reserved < eps_net:
            reject("Insufficient Energy for NET", fatal=False)
            return

        R = Wn + d_net
        if R > D_r:
            selected_server.record_rejected_task(task_id, task_type, arrival_time_system, image_size,
                                                 "Deadline Exceeded", d_cpu)

            result_sink["ok"] = False
            return

        selected_server.energy_reserved += eps_net

        req_net = selected_server.net_dev.request()
        qlen_on_enqueue_net = _res_len_with_users(selected_server.net_dev)
        if ENABLE_MONITORING:
            req_net.task_data = task_OBS
        print(
            f"[{env.now:.3f}] Batch {task_id} enqueued on NET {selected_server.name} qlen_enqueue_net={qlen_on_enqueue_net}")

        yield req_net

        # === FIX: Check Energia NET POST-Coda ===
        if selected_server.energy < eps_net:
            selected_server.energy_reserved -= eps_net
            selected_server.net_dev.release(req_net)
            reject("Insufficient Energy for NET", fatal=True)
            return
        # ========================================

        time_in_queue = env.now - arrival_time_task_queue
        selected_server.net_busy_until = env.now + net_time

        yield env.timeout(net_time)
        selected_server.net_busy_until = env.now
        if not routing_already_charged:
            selected_server.energy -= eps_net
        else:
            # opzionale: se vuoi, registra che la routing-energy è stata già presa da `routing_energy_from`.
            pass
        selected_server.energy_reserved -= eps_net
        selected_server.net_dev.release(req_net)

    # Fine dei branch: calcola metriche finali e registra il completamento
    end_time = env.now
    execution_time = end_time - start_time
    service_time = execution_time + transfer_time + time_in_queue

    selected_server.tasks.append(task_OBS)
    globals.gbl_tasks.append(task_OBS)

    if selected_server.elev_angle < config["Phi_max"] - config["Phi_buffer"]:
        task_OBS.label = 'SEN_OUT_OF_BUFF'
        if ENABLE_MONITORING:
            print(f"{selected_server} {selected_server.elev_angle}° {task_OBS.id} set as {task_OBS.label}")

    # fallback per qlen: usa i valori di enqueue catturati, altrimenti misura lo stato attuale
    cpu_q = qlen_on_enqueue_cpu if qlen_on_enqueue_cpu is not None else _res_len_with_users(selected_server.cpu_dev)
    net_q = qlen_on_enqueue_net if qlen_on_enqueue_net is not None else _res_len_with_users(selected_server.net_dev)
    qlen = cpu_q + net_q

    # Passa il tipo di task e i valori energetici
    selected_server.task_completed(
        task_id, task_type, arrival_time_system, arrival_time_task_queue,
        start_time, end_time, execution_time, service_time, time_in_queue,
        selected_server.name, num_hops, qlen, transfer_time,
        image_size, DeadLine=False, exec_after_set=False,
        eps_cpu=eps_cpu, eps_net=eps_net
    )
    result_sink["ok"] = True
    result_sink["reason"] = None
    result_sink["fatal"] = False

    if ENABLE_MONITORING:
        print(
            f"[{env.now:.3f}] Task {task_id} served on {selected_server.name} qlen_enqueue_cpu={qlen_on_enqueue_cpu} qlen_enqueue_net={qlen_on_enqueue_net} time_in_queue={time_in_queue:.3f}")


def _merge_queues_for_ilp(sat_state: dict):
    """
    Unisce waiting+service per CPU/NET e mappa i task nel formato atteso dall'ILP.
    - CPU: usa 'demand' -> d_cpu (s)
    - NET: usa image_size_MB come d_net (MB) quando disponibile; come fallback usa 'demand' (MB)
    Ritorna: (cpu_queue, net_queue)
    """
    cpu_q = []
    net_q = []

    # CPU
    for key in ("queue_cpu_waiting", "queue_cpu_service"):
        for t in sat_state.get(key, []):
            cpu_q.append(QueueTask(
                task_id=str(t.get("task_id")),
                d_cpu=float(t.get("demand", 0.0) or 0.0),
                d_net=0.0,  # non usato per la coda CPU
                D=float(t.get("deadline", 300.0) or 300.0)
            ))

    # NET
    for key in ("queue_net_waiting", "queue_net_service"):
        for t in sat_state.get(key, []):
            d_net_MB = t.get("image_size_MB", None)
            if d_net_MB is None or d_net_MB == 'N/A':
                # fallback: prendi 'demand' come MB
                d_net_MB = float(t.get("demand", 0.0) or 0.0)
            net_q.append(QueueTask(
                task_id=str(t.get("task_id")),
                d_cpu=0.0,
                d_net=float(d_net_MB),
                D=float(t.get("deadline", 300.0) or 300.0)
            ))

    return cpu_q, net_q


def _build_snapshot_Ek(env, candidate_servers, current_task_id, d_cpu_req, d_net_MB_req, deadline_req,
                       config):
    """
    Costruisce un oggetto Snapshot minimale per solve_on_Ek su Ek={k}+neighbors[k].
    - d_net del *task corrente* è espresso in MB (l'ILP lo converte in secondi con la banda).
    - B = energia corrente; B_max = initial_energy (fallback dal config).
    """
    sen_map = {}
    neighbors_map = {}
    listening = []

    # costruisci mappe SEN e vicinato
    for srv in candidate_servers:
        st = srv.export_state(env)  # richiede enable_queue_monitoring=true
        B = float(st.get("energy_budget_J", getattr(srv, "energy", 0.0)))
        B_max = float(config.get("initial_energy", B if B > 0 else 1.0))

        cpu_q, net_q = _merge_queues_for_ilp(st)

        # NOTA: l'ILP usa default_net_bw_MBps, quindi non è obbligatorio impostare s.net_bw_MBps
        # ma se vuoi puoi passare un valore medio per nodo come attributo add-on:
        s_state = SENState(
            B=B,
            B_max=B_max,
            Rmax_norm=1.0,
            cpu_queue=cpu_q,
            net_queue=net_q,
            in_service_cpu=None,
            in_service_net=None,
            net_bw_bps=None,  # lasciamo None -> userà default_net_bw_MBps
        )
        sen_map[srv.name] = s_state

        # vicini: basta la lista dei nomi
        neigh_names = []
        for n in srv.get_neighbors():
            neigh_names.append(n.name)
        neighbors_map[srv.name] = neigh_names

        # listening dome (se serve per policy): lo prendiamo dallo state
        if st.get("in_listening_dome", False):
            listening.append(srv.name)

    # richieste: SOLO il task corrente
    req = QueueTask(
        task_id=str(current_task_id),
        d_cpu=float(d_cpu_req),
        d_net=float(d_net_MB_req),  # MB!
        D=float(deadline_req)
    )

    snap = Snapshot(
        time=float(env.now),
        listening_dome=listening,
        neighbors=neighbors_map,
        sen=sen_map,
        requests=[req],
        pre_R={},  # niente precomputation
        pre_E={}
    )
    return snap


# assicurati di avere in alto (una sola volta) inizializzato il dict globale:
# globals.gbl_task_hops = {}  # inizializzato all'import sopra
# globals.gbl_task_final_hops = {}  # inizializzato all'import sopra

def _finalize_and_assign_task(env, server_selected, server, task_id,
                              image_size, Volume_size, arrival_time_system,
                              transfer_time, task_type, d_cpu, deadline,
                              required_ram, required_disk,
                              initial_server_counter, different_server_counter, other_server_counter,
                              net_bw_override_Bps=None,
                              result_sink=None,
                              allow_retry=False):
    """
    Blocco finale: aggiorna contatori, calcola energia di routing,
    registra i dati globali e chiama TaskAssignment.
    """
    # Inizializza result_sink se necessario
    if result_sink is None:
        result_sink = {}

    # Incremento del contatore "candidature osservate"
    initial_server_counter[server_selected.name] += 1

    # Assicurati che esista il contatore per questo task
    globals.gbl_task_hops.setdefault(str(task_id), 0)

    data_bytes_global = (image_size + Volume_size) * (1024 ** 2)
    bw_Bps_global = bw_Bps

    # Calcoliamo se avviene un trasferimento fisico e, in tal caso,
    # applichiamo l'energia e incrementiamo il contatore per-task.
    num_hops_local = globals.gbl_task_hops.get(str(task_id), 0)

    if server != server_selected:
        # Qui stiamo per effettuare un transfer dal server_selected --> server
        # calcola energia di routing
        bw_MBps_link = server_selected.get_bandwidth(server)

        if bw_MBps_link is not None and bw_MBps_link > 0:
            bw_Bps_for_energy = bw_MBps_link * (1024 ** 2)
            data_bytes_for_energy = data_bytes_global
        else:
            bw_Bps_for_energy = bw_Bps_global if bw_Bps_global > 0 else 0
            data_bytes_for_energy = data_bytes_global

        if bw_Bps_for_energy > 0:
            Ptrasm = config.get("Ptrasm", 1.0)
            eps_net = server_selected.compute_routing_energy(data_bytes_for_energy, bw_Bps_for_energy, Ptrasm)
        else:
            eps_net = 0.0

        # >>> FIX: CONTROLLO ENERGIA ROUTING <<<
        if server_selected.energy < eps_net:
            print(
                f"[{env.now:.2f}] [Task {task_id}] Routing FAIL {server_selected.name}->{server.name}: No Energy for TX")
            if result_sink is not None:
                result_sink["ok"] = False
                result_sink["reason"] = "Routing Energy Exhausted"
                result_sink["fatal"] = True  # Non ha senso riprovare se la sorgente è morta

            # Registra il rifiuto per statistiche
            server_selected.record_rejected_task(task_id, task_type, arrival_time_system, image_size,
                                                 "Routing Energy Exhausted", d_cpu)
            return
        # >>> FINE FIX <<<

        # SOTTRAI ENERGIA: ora è sicuro farlo
        server_selected.energy -= eps_net
        print(f"[{env.now:.2f}] [Task {task_id}] Routed {server_selected.name} -> {server.name} | "
              f"E_NET={eps_net:.6f} J | Remaining={server_selected.energy:.2f} J")

        # ---- Qui decidiamo la semantica: contare hop come "performed transfer" ----
        globals.gbl_task_hops[str(task_id)] = globals.gbl_task_hops.get(str(task_id), 0) + 1
        num_hops_local = globals.gbl_task_hops[str(task_id)]

        # Aggiornamento dei contatori di fallback/diversi (rimangono invariati)
        different_server_counter[server_selected.name] += 1
        other_server_counter[server.name] += 1

    # Prepara i dati di log (usiamo num_hops_local attuale)
    task_data = {
        "task_id": task_id,
        "arrival_time": arrival_time_system,
        "type": task_type,
        "ram": required_ram,
        "disk": required_disk,
        "image_size": image_size,
        "exec_time": d_cpu,
        "transfer_time": transfer_time,
        "performed_transfers": globals.gbl_task_hops.get(str(task_id), 0),
        "final_hops": None,
        "execution_server": server.name
    }

    # Chiamiamo TaskAssignment_ILP passando il num_hops_local
    local_sink = result_sink if result_sink is not None else {}
    yield from TaskAssignment_ILP(env, server, task_id, image_size,
                                  arrival_time_system, num_hops_local, transfer_time,
                                  task_type, d_cpu, deadline,
                                  net_bw_override_Bps=net_bw_override_Bps,
                                  result_sink=local_sink,
                                  allow_retry=allow_retry, routing_already_charged=True, routing_energy_from=server_selected.name)

    # Registra i dati globali: salva il conteggio finale SOLO se assegnato con successo
    ok_assigned = bool(local_sink.get("ok", False))
    performed = globals.gbl_task_hops.get(str(task_id), 0)

    if ok_assigned:
        globals.gbl_task_final_hops[str(task_id)] = performed
        task_data["final_hops"] = performed
        globals.gbl_generated_tasks_data.append(task_data)
    else:
        reason = local_sink.get("reason")
        print(f"[{env.now:.2f}] [Task {task_id}] assignment failed on {server.name} reason={reason}")

    return



def SearchNode_ILP_Hybrid_v2(env, server_selected, task_id, required_ram, required_disk, image_size, Volume_size,
                             arrival_time_system,
                             initial_server_counter, different_server_counter, other_server_counter,
                             task_type, max_energy, d_cpu, deadline):
    """
    Wrapper: Prova ILP se configurato in config["SearchNode"].
    Altrimenti, o in caso di fallimento, esegue SearchNode_Heuristic_v1.
    """

    if str(config.get("SearchNode", "")).upper() == "ILP":

        # --- Inizio blocco ILP  ---
        print(f"[{env.now:.2f}] [Task {task_id}] Entering ILP branch (v2)")

        # 1. Ottieni i vicini (necessario per ILP)
        neighbors_at_distance_one = list(server_selected.get_neighbors())
        if server_selected not in neighbors_at_distance_one:
            neighbors_at_distance_one.append(server_selected)

        d_net_MB_req = float(image_size + (Volume_size or 0.0))

        # Snapshot locale e risoluzione
        snap = _build_snapshot_Ek(
            env=env,
            candidate_servers=neighbors_at_distance_one,
            current_task_id=task_id,
            d_cpu_req=float(d_cpu),
            d_net_MB_req=float(d_net_MB_req),
            deadline_req=float(deadline),
            config=config
        )

        # --- Coefficienti fisici ---
        P_net = float(config.get("Ptrasm", 1.0))
        alpha_cpu, alpha_net_J_per_byte = alpha_from_physics(C_sen, e_coeff, P_net, bw_Bps)
        alpha_net = alpha_net_J_per_byte * (1024 ** 2)  # J/MB
        w_e = float(config.get("ilp_weights", {}).get("w_e", 0.5))
        w_R = float(config.get("ilp_weights", {}).get("w_R", 0.5))

        # --- Funzione interna per estrarre risultati ---
        def _extract_chosen_server(ilp_res, task_id):
            # ... (la tua logica di estrazione è corretta) ...
            tid = str(task_id)
            if isinstance(ilp_res, dict):
                a = ilp_res.get("assignments")
                if isinstance(a, list):
                    for item in a:
                        if str(item.get("task") or item.get("task_id") or item.get("id")) == tid:
                            sen = item.get("sen") or item.get("server")
                            if sen: return str(sen)
                    if len(a) == 1 and isinstance(a[0], dict):
                        sen = a[0].get("sen") or a[0].get("server")
                        if sen: return str(sen)
                if isinstance(a, dict):
                    v = a.get(tid)
                    if v is not None: return str(v)
                for key in ("solution", "assignments_list", "result", "x"):
                    if key in ilp_res:
                        v = _extract_chosen_server(ilp_res[key], task_id)
                        if v: return v
            if isinstance(ilp_res, list):
                for item in ilp_res:
                    if isinstance(item, (tuple, list)) and len(item) >= 2 and str(item[0]) == tid:
                        return str(item[1])
                    if isinstance(item, dict):
                        itid = str(item.get("task_id") or item.get("task") or item.get("id") or "")
                        if itid == tid:
                            sen = item.get("sen") or item.get("server") or item.get("assignment") or item.get("value")
                            if sen: return str(sen)
                if len(ilp_res) == 1 and isinstance(ilp_res[0], str):
                    return ilp_res[0]
            if isinstance(ilp_res, str):
                return ilp_res
            return None

        # ====== CHIAMATA AL SOLVER ======
        objective_mode = str(config.get("ilp_objective", "weighted")).lower()
        if objective_mode == "hierarchical":
            ilp_res = solve_on_Ek_hierarchical(
                snapshot=snap, k=server_selected.name, picked_tasks=[str(task_id)],
                primary=str(config.get("lexi_primary", "energy")).lower(),
                tol=float(config.get("lexi_tol", 0.10)),
                alpha_cpu=alpha_cpu, alpha_net=alpha_net,
                solver_name=str(config.get("ilp_solver", "CBC")),
                default_net_bw_MBps=bw_MBps,
                debug=bool(config.get("ilp_debug", False)),
                tasks_from_prof=False
            )
        else:
            ilp_res = solve_on_Ek(
                snapshot=snap, k=server_selected.name, picked_tasks=[str(task_id)],
                w_energy=w_e, w_time=w_R,
                alpha_cpu=alpha_cpu, alpha_net=alpha_net,
                solver_name="CBC", use_node_Rmax_norm=False,
                default_net_bw_MBps=bw_MBps,
                debug=False, tasks_from_prof=False
            )

        print(f"[ILP] res_type={type(ilp_res).__name__} value_preview={str(ilp_res)[:160]}")
        chosen_server_name = _extract_chosen_server(ilp_res, task_id)

        # --- Finalizzazione ILP (se ha successo) ---
        cfg_fb = config.get("fallback_retry", {"enabled": True, "max_attempts": 3, "deadline_slack": 0.05})
        fallback_enabled = bool(cfg_fb.get("enabled", True))
        fallback_attempts = int(cfg_fb.get("max_attempts", 3))
        deadline_slack = float(cfg_fb.get("deadline_slack", 0.0))

        neighbors = list(neighbors_at_distance_one)  # includi anche server_selected

        def _try_assign_once(exec_server,
                             force_transfer_time=None,
                             force_bw_Bps=None):
            lat = server_selected.get_latency(exec_server)
            bw_MBps_link = server_selected.get_bandwidth(exec_server)

            if force_transfer_time is not None:
                transfer_time_local = force_transfer_time
                net_bw_override_Bps = force_bw_Bps
            else:
                if (lat is not None) and (bw_MBps_link is not None) and bw_MBps_link > 0:
                    transfer_time_local = ((image_size + Volume_size) * (1024 ** 2)
                                           / (bw_MBps_link * (1024 ** 2))) + lat
                    net_bw_override_Bps = bw_MBps_link * (1024 ** 2)
                else:
                    transfer_time_local = 0.0
                    net_bw_override_Bps = None

            rs = {}
            yield from _finalize_and_assign_task(
                env, server_selected, exec_server, task_id,
                image_size, Volume_size, arrival_time_system,
                transfer_time_local, task_type, d_cpu, deadline,
                required_ram, required_disk,
                initial_server_counter, different_server_counter, other_server_counter,
                net_bw_override_Bps=net_bw_override_Bps,
                result_sink=rs,
                allow_retry=True  # <--- qui è la chiave
            )
            return rs.get("ok", False), rs.get("reason"), rs.get("fatal", False)

        # ====== CASO 1: ILP ha scelto un server ======
        if chosen_server_name:
            print(f"[{env.now:.2f}] [Task {task_id}] ILP chose server: {chosen_server_name}")
            server = next((s for s in neighbors if s.name == chosen_server_name), server_selected)

            ok, reason, fatal = (yield from _try_assign_once(server))
            if ok:
                return

            # se il fallimento è fatale (es. Deadline Exceeded),
            # non ha senso riprovare su altri SEN
            if fatal:
                return

            # altrimenti posso riprovare su altri SEN a caso (fallback CASO 1)
            if not fallback_enabled or fallback_attempts <= 0:
                return

            excluded = {server.name}
            attempts = 0
            assigned = False
            last_reason = reason

            while attempts < fallback_attempts and not assigned:
                attempts += 1
                available = [s for s in neighbors if s.name not in excluded]
                if not available:
                    break

                sen_alt = globals.rnd.choice(available)
                excluded.add(sen_alt.name)

                print(f"[FALLBACK-ILP-C1][{env.now:.2f}] Task {task_id} tenta su {sen_alt.name} (tentativo {attempts})")
                ok_alt, reason_alt, fatal_alt = (yield from _try_assign_once(sen_alt))
                if ok_alt:
                    assigned = True
                else:
                    last_reason = reason_alt or last_reason
                    if fatal_alt:
                        break  # il prossimo sarebbe inutile

            # se anche dopo il fallback non è stato assegnato, hai già i rifiuti/log interni:
            return

        # ====== CASO 2: ILP infeasible/none ======
        print(f"[{env.now:.2f}] [Task {task_id}] ILP infeasible/none → fallback random (ILP-only).")
        if not fallback_enabled or fallback_attempts <= 0:
            return

        excluded = set()
        attempts = 0
        assigned = False

        while attempts < fallback_attempts and not assigned:
            attempts += 1

            # prendo la lista di candidati ancora non provati
            available = [s for s in neighbors if s.name not in excluded]
            if not available:
                print(f"[FALLBACK-ILP][{env.now:.2f}] Task {task_id}: nessun neighbor disponibile per il fallback.")
                break

            # scelgo un SEN a caso
            sen_alt = globals.rnd.choice(available)
            excluded.add(sen_alt.name)

            # calcolo transfer_time e banda come fai normalmente
            lat = server_selected.get_latency(sen_alt)
            bw_MBps_link = server_selected.get_bandwidth(sen_alt)

            if (lat is not None) and (bw_MBps_link is not None) and bw_MBps_link > 0:
                bw_Bps_link_alt = bw_MBps_link * (1024 ** 2)
                total_data_bytes = (image_size + Volume_size) * (1024 ** 2)
                transfer_time_alt = (total_data_bytes / bw_Bps_link_alt) + lat
            else:
                bw_Bps_link_alt = None
                transfer_time_alt = 0.0

            print(f"[FALLBACK-ILP][{env.now:.2f}] Task {task_id} tenta su {sen_alt.name} (tentativo {attempts})")

            # provo ad assegnare davvero il task su questo SEN
            result_sink = {}
            yield from _finalize_and_assign_task(
                env, server_selected, sen_alt, task_id,
                image_size, Volume_size, arrival_time_system,
                transfer_time_alt, task_type, d_cpu, deadline,
                required_ram, required_disk,
                initial_server_counter, different_server_counter, other_server_counter,
                net_bw_override_Bps=bw_Bps_link_alt,
                result_sink=result_sink
            )

            assigned = bool(result_sink.get("ok", False))

        # se anche dopo i tentativi fallback non ho assegnato, lascio che i rifiuti
        # registrati dentro TaskAssignment restino come "verdetto finale"
        return

    # =========================
    # BRANCH: EURISTICA (chiamata a v1)
    # Motivi per essere qui:
    # 1. config["SearchNode"] non era "ILP"
    # 2. config["SearchNode"] era "ILP" ma il solver ha fallito
    # =========================

    print(f"[{env.now:.2f}] [Task {task_id}] Entering HEURISTIC branch (v2) -> calling v1")

    # Passiamo tutti gli argomenti originali a v1, che è funzione euristica standard.
    '''yield from simulation.SearchNode_Heuristic_v1(
        env, server_selected, task_id, required_ram, required_disk, image_size, Volume_size,
        arrival_time_system,
        initial_server_counter, different_server_counter, other_server_counter,
        task_type, max_energy, d_cpu, deadline
    )'''