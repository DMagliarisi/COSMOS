import csv
import json5
import os
import sys
import simpy
import json
from EdgeServer import build_task_csv_path
from enums import AccPointMode
import ILP_simulation, simulation
from Task import findAlgorithm, generate_Tasks_Status, convert_task_list_in_dict
from simulation import generate_tasks
from topology import build_configurations, get_global_mode, load_saved_configuration, loadConfiguration, \
    periodic_recall_Topology_monitor, string_to_skyfield_time, loadConfiguration_simple
from user_based_topology import get_current_time, getObserverObj
from SaveCurrentSATOnFile import saveTLEOnFile
from routing_Manager import load_saved_OGM, periodic_recall_Routing_monitor
from Observer import Observer
from simulation_OGM import process_OGM_enviroment_simulation, remove_first_30_configurations
import globals
import time

from utils import colorize

simulation_dataset = []


def _win_longpath(p: str) -> str:
    """Rende il path compatibile con i percorsi lunghi di Windows usando il prefisso \\?\\."""
    if os.name != "nt":
        return p
    p = os.path.abspath(p)
    if p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):  # UNC path
        return "\\\\?\\UNC" + p[1:]
    return "\\\\?\\" + p


# Aggiungi il nuovo processo di raccolta dati
def data_collector(env, interval, start_time, end_time):
    """
    Processo per la raccolta periodica dei dati dello stato della rete
    in un intervallo di tempo specificato.
    """
    global simulation_dataset
    simulation_dataset = {
        "metadata": {
            "battery_life": globals.config.get("initial_energy", 0.0)
        },
        "snapshots": []
    }
    # Attendi fino all'inizio dell'intervallo di raccolta
    yield env.timeout(start_time)

    while env.now <= end_time:
        # Crea un 'snapshot' dello stato attuale
        snapshot = {
            "time": env.now,
            "satellites": []
        }

        # Scansiona tutti i satelliti e raccogli i dati
        for sat_obj in globals.edge_servers:
            if sat_obj:
                state = sat_obj.export_state(env)
                if state.get("neighbors_count") > 0:
                    snapshot["satellites"].append(state)

        # Aggiungi lo snapshot alla lista globale
        simulation_dataset["snapshots"].append(snapshot)

        # Attendi l'intervallo di tempo specificato prima della prossima raccolta
        yield env.timeout(interval)


if __name__ == "__main__":
    # Imposta seme e ambiente
    start_time_simulation_real = time.time()
    env = simpy.Environment()
    config, resolution_config = globals.config, globals.resolution_config
    print(
        f"[INFO] Using config file: {globals.config_file_path if hasattr(globals, 'config_file_path') else 'unknown'}")

    MaxTry = config.get("max_try", 10)
    r_algo = None
    # 1) Costruzione configurazioni
    if config.get("Build_Configurations", False):

        configurations_base, configuration_optimal = None, None

        if not config.get("load_saved_configuration"):
            tle_data = saveTLEOnFile()
            t0 = get_current_time()
            configurations_base = build_configurations(t0, tle_data, AccPointMode.BASE)
            configuration_optimal = build_configurations(t0, tle_data, AccPointMode.OPTIMAL)
        else:
            # FASE TEST : Se abbiamo già creato le configurazioni le carichiamo
            configurations_base = load_saved_configuration(AccPointMode.BASE)
            configuration_optimal = load_saved_configuration(AccPointMode.OPTIMAL)

        if config["redistribuite_OGM"]:
            process_OGM_enviroment_simulation(configurations_base)
            remove_first_30_configurations(AccPointMode.BASE)
            remove_first_30_configurations(AccPointMode.OPTIMAL)

        sys.exit("File of configurations created")

    # 2) Caricamento o creazione topologia
    if config.get("Load_Configuration", False):
        mode = get_global_mode()  # Otteniamo la Modalità di simulazione
        r_algo = findAlgorithm()
        simple_exec = True

        globals.data_configurations = load_saved_configuration(mode)

        if r_algo == "BATMAN" or r_algo == "DINAMICO":
            # Se l'algoritmo richiede le OGM TABLE
            globals.OGMs_tables = load_saved_OGM()
            globals.edge_servers, globals.global_access_point = loadConfiguration(env, globals.data_configurations,
                                                                                  globals.OGMs_tables)
        else:
            globals.edge_servers, globals.global_access_point = loadConfiguration_simple(env,
                                                                                         globals.data_configurations)

        env.process(periodic_recall_Topology_monitor(env, globals.data_configurations, globals.OGMs_tables))
    else:
        sys.exit("Nessuna Configurazione richiesta!")

    globals.observer = Observer(env, getObserverObj())  # Singleton Observer
    env.process(periodic_recall_Routing_monitor(env))

    if not globals.data_configurations:
        sys.exit("Errore: il file delle configurazioni è vuoto.")
    skyfield_time = string_to_skyfield_time(globals.data_configurations["t0"])

    globals.initial_server_counter = {
        server.name: 0 for server in globals.edge_servers}
    globals.different_server_counter = {
        server.name: 0 for server in globals.edge_servers}
    globals.other_server_counter = {
        server.name: 0 for server in globals.edge_servers}

    # 3 batch in rete
    batch_task_id = 1000  # base id per batch
    for server in globals.edge_servers:
        n_batches = globals.rnd.randint(0, 3)  # uno o più batch per server (range 0..3)
        for _ in range(n_batches):
            batch_task_id += 1000

            # requisiti (ram/disk) se vuoi tenerli
            required_ram = globals.rnd.randint(config["required_ram"]["min"], config["required_ram"]["max"])
            required_disk = globals.rnd.randint(config["required_disk"]["min"], config["required_disk"]["max"])

            # scegli image_size in base alle regole BATCH del tuo resolution_config
            ranges = resolution_config["size_ranges_MB"]
            batch_params = ranges["BATCH_TASK"]
            r = globals.rnd.random()
            if r < batch_params["gamma_H_weight"]:
                image_size = globals.rnd.uniform(*batch_params["H_range"])
            else:
                image_size = globals.rnd.uniform(*batch_params["VH_range"])

            # d_cpu = 0 per batch
            d_cpu_batch = 0.0
            bw_Bps, data_bytes = simulation.network_metrics(image_size)
            d_net_batch = data_bytes / bw_Bps if bw_Bps > 0 else float('inf')
            delta_D = config.get("delta_D", 0.2)
            deadline_batch = (1.0 + delta_D) * (d_cpu_batch + d_net_batch)

            # --- SELEZIONE MODALITÀ BATCH (ILP vs ERT) ---

            if str(config.get("SearchNode", "")).upper() == "ILP":
                # Nota: Passiamo batch_task_id come INTERO (rimosso str()) per evitare TypeError nel sort finale
                env.process(ILP_simulation.TaskAssignment_ILP(
                    env,
                    server,
                    batch_task_id,  # <--- FIX: RIMOSSO str(), ora è INT
                    image_size,
                    env.now,
                    0,
                    0.0,
                    "Batch",
                    0.0,
                    deadline_batch,
                    net_bw_override_Bps=None,
                    result_sink={},
                    allow_retry=False
                ))
            elif str(config.get("SearchNode", "")).upper() == "ERT":
                # Modalità ERT: usa simulation.TaskAssignment
                env.process(simulation.TaskAssignment(
                    env,
                    server,
                    batch_task_id,  # INT
                    image_size,
                    env.now,
                    0,
                    0.0,
                    "Batch",
                    0.0,
                    deadline_batch
                ))
            else:
                # Modalità ERT/Standard: usiamo la funzione di simulation.py
                env.process(simulation.enqueue_batch_in_net(
                    env,
                    server,
                    batch_task_id,  # <--- Già INT
                    image_size,
                    env.now,
                    deadline_batch  # Passiamo la deadline assoluta o relativa a seconda di come la gestisce la func
                ))
            # -----------------------------------------------------------------------------------------------

    print("Batch tasks for all servers scheduled (0..3 per server).")

    # 4) Avvia la generazione dei task
    env.process(generate_tasks(
        env,
        globals.initial_server_counter,
        globals.different_server_counter,
        globals.other_server_counter
    ))

    # 5) Preparazione dei nomi di cartella e file
    ap = config.get("access_point", 0)
    seed_val = config["seed"]
    gen_dist = config["generate_tasks"]["distribution"]
    req_dist = config["request_distribution"]["distribution"]
    if req_dist == "0_0_0":
        req_dist = "RR"
    atime = config["arrival_time_exponential"]
    cpu_mean = config["CPU_timeout"]["gen"]["mean"]
    solver = config["SearchNode"]
    ap_dir_bidir = config["AP_routing_bidirectional"]  # (Booleano) AP_Routing
    ap_selection = config["AP_selection"]
    energy_budget = config["initial_energy"]
    deadline = config["deadline"]
    ilp_w_e, ilp_w_R = config["ilp_weights"]["w_e"], config["ilp_weights"]["w_R"]
    complete_sim_solver = None

    if req_dist == "DTS-base" and ap_selection == "base" and solver == "ERT":
        complete_sim_solver = "DTS-base"
    elif req_dist == "DTS-base" and ap_selection == "optimal" and solver == "ERT":
        complete_sim_solver = "DTS-APopt"
    elif req_dist == "OrbitAware" and ap_selection == "optimal" and solver == "ERT":
        complete_sim_solver = "OrbitAware"
    elif req_dist == "DTS-base" and ap_selection == "optimal" and solver == "ILP":
        complete_sim_solver = "ILP"
    else:
        sys.exit(
            f"Complete_sim_solver not right! CHECK: req_dist:{req_dist} ap_selection:{ap_selection} solver:{solver}")

    # BETA, ALPHA, GAMMA
    beta = resolution_config["beta_probabilities"]
    bg, bcpui, bcpudi = beta["Generic_Service"], beta["CPU_Intensive"], beta["CPU_and_Data_Intensive"]
    alpha = resolution_config["size_ranges_MB"]["CPU_DATA_INTENSIVE"]
    am, ah, avh = alpha["alpha_M_weight"], alpha["alpha_H_weight"], alpha["alpha_VH_weight"]
    gamma = resolution_config["size_ranges_MB"]["BATCH_TASK"]
    gh, gvh = gamma["gamma_H_weight"], gamma["gamma_VH_weight"]
    img_res_dir = f"IMG_RES_bg_{bg}_bcpui_{bcpui}_bcpudi_{bcpudi}_am_{am}ah_{ah}_avh_{avh}_gh_{gh}_gvh_{gvh}"

    # Cartella base: include modalità, AP e seed
    base_dir = ""
    if complete_sim_solver == "ILP":
        base_dir = f"result/{complete_sim_solver}_sim_SystemAP{ap}/w_e{ilp_w_e}_wR_{ilp_w_R}/deadline_{deadline}/Energy_budget_{energy_budget}/{img_res_dir}/Routing_bidirectional_{ap_dir_bidir}/seed_{seed_val}/r_algo_{r_algo}/we_{ilp_w_e}_wR_{ilp_w_R}/"
    else:
        base_dir = f"result/{complete_sim_solver}_sim_SystemAP{ap}/deadline_{deadline}/Energy_budget_{energy_budget}/{img_res_dir}/Routing_bidirectional_{ap_dir_bidir}/seed_{seed_val}/r_algo_{r_algo}/"
    os.makedirs(base_dir, exist_ok=True)

    # File CSV e log con nomenclatura completa
    csv_task = (
        f"{base_dir}/results_"
        f"{gen_dist}_REQ-{complete_sim_solver}_"
        f"AT_{atime}_CPU_{cpu_mean}.csv"
    )
    csv_mig = (
        f"{base_dir}/migration_"
        f"{gen_dist}_REQ-{complete_sim_solver}_"
        f"AT_{atime}_CPU_{cpu_mean}.csv"
    )
    csv_routing_task = build_task_csv_path(base_dir, atime, cpu_mean)


    def _ensure_parent_dir(path_str: str) -> str:
        parent = os.path.dirname(path_str) or "."
        parent = os.path.abspath(parent)
        os.makedirs(_win_longpath(parent), exist_ok=True)
        return os.path.join(parent, os.path.basename(path_str))


    csv_task = _ensure_parent_dir(csv_task)
    csv_mig = _ensure_parent_dir(csv_mig)
    csv_routing_task = _ensure_parent_dir(csv_routing_task)

    # Salvo il nome del CSV nel config per eventuali moduli esterni
    config["csv_name"] = {"name": csv_task}
    # Scrivo la config *usata* nella cartella di output della simulazione
    used_config_path = os.path.join(base_dir, "used_config.json5")
    os.makedirs(os.path.dirname(used_config_path), exist_ok=True)
    with open(used_config_path, 'w') as wf:
        json5.dump(config, wf, indent=2)
    print(f"[INFO] Config salvata in: {used_config_path}")

    print(f"--- Avvio simulazione ---")
    print(f"Cartella: {base_dir}")
    print(f"CSV tasks: {csv_task}")
    print(f"CSV migrazioni: {csv_mig}")

    # esempio: ogni 2s, dal secondo 100 al 200
    env.process(data_collector(env, interval=2, start_time=100, end_time=200))

    # 6) Esecuzione simulazione
    env.run(config['simulation_duration'])

    # Definisci il nome del file di output
    output_folder = "Generated_datasets"

    if config.get("save_generated_tasks_dataset", True) and globals.gbl_generated_tasks_data:
        file_name = "generated_tasks_counter.json5"
        # Salviamo dentro la cartella specifica della simulazione
        output_path = os.path.join(base_dir, file_name)

        print(f"\nSalvataggio del dataset generato in: {output_path}")

        try:
            with open(output_path, 'w') as f:
                json5.dump(globals.gbl_generated_tasks_data, f, indent=4)
            print("Salvataggio completato con successo.")
        except Exception as e:
            print(f"ERRORE nel salvataggio del dataset: {e}")

    # Salva il dataset alla fine della simulazione
    ENABLE_MONITORING = config.get("enable_queue_monitoring", False)
    if ENABLE_MONITORING:
        monitor_file_name = "simulation_dataset.json"
        output_path_monitor = os.path.join(base_dir, monitor_file_name)
        with open(output_path_monitor, "w") as f:
            json.dump(simulation_dataset, f, indent=4)
        print("\nDataset dello stato della simulazione salvato in 'simulation_dataset.json'")

    # Stampa il riassunto dei task usando la funzione dell'Observer
    globals.observer.print_task_summary()
    print("-" * 10)
    generate_Tasks_Status(csv_routing_task, base_dir)
    task_dict = convert_task_list_in_dict(globals.gbl_tasks)

    # ---------------------------------------------------------------------
    # 7) Scrittura risultati su CSV (Task completati/rifiutati/residui)
    # ---------------------------------------------------------------------

    # Raccogli tutti i server in un'unica lista per l'iterazione
    all_servers_by_name = {}
    for s in (globals.edge_servers or []) + (globals.global_access_point or []):
        if s is None:
            continue
        all_servers_by_name[s.name] = s
    all_servers = list(all_servers_by_name.values())

    with open(csv_task, mode='w', newline='') as f_out:
        writer = csv.writer(f_out)
        # Intestazione aggiornata a 27 colonne
        writer.writerow([
            "Task ID", "Task Type", "Status", "Arrival Time (System)",
            "Arrival Time (Queue)", "Start Time", "End Time", "Execution time",
            "Service Time", "Time in system", "Time in queue", "Server Name", "Num Hops",
            "Num Hops Routing",  # <-- Nuova colonna
            "Queue length", "transfer_time", "Image_Size_MB", "Exec_after_set",
            "Energy_CPU [J]", "Energy_NET [J]", "Energy_TOTAL [J]", "Remaining_energy [J]", "Remaining_energy [%]",
            "Rejection Reason",
            "Routing Init Time",  # <-- Nuova colonna
            "Routing End Time",  # <-- Nuova colonna
            "Routing Duration"  # <-- Nuova colonna
        ])

        initial_energy_for_percent = config.get("initial_energy", 0.0)

        for srv in all_servers:
            # completed tasks (Questa sezione era già corretta)
            for entry in getattr(srv, 'completed_tasks', []):

                (tid, task_type, arr_sys, arr_q, start_t, end_t, ex_t, service_t,
                 time_q, sel_srv, hops, qlen, tranfer_t, image_size,
                 DeadLine, exec_set, eps_cpu, eps_net, eps_tot, srv_rem_energy) = entry

                time_in_system = (end_t - arr_sys) if (
                        isinstance(end_t, (int, float)) and isinstance(arr_sys, (int, float))) else "N/A"

                remaining_percent = "N/A"
                if isinstance(srv_rem_energy, (int, float)) and initial_energy_for_percent > 0:
                    remaining_percent = (srv_rem_energy / initial_energy_for_percent) * 100

                # Ottieni i dati di routing dal dizionario
                r_hops, r_init_time, r_end_time, r_duration = "N/A", "N/A", "N/A", "N/A"  # Default
                if tid in task_dict:
                    try:
                        r_hops, r_init_time, r_end_time, r_duration = task_dict[tid].get_stat_csv()
                    except Exception as e:
                        print(f"Warning: could not get routing stats for task {tid}: {e}")

                writer.writerow([
                    tid, task_type, "Completed", arr_sys, arr_q, start_t, end_t,
                    ex_t, service_t, time_in_system, time_q, sel_srv, hops,
                    r_hops,  # <-- Valore Routing Hops
                    qlen, tranfer_t, image_size, exec_set,
                    eps_cpu, eps_net, eps_tot, srv_rem_energy, remaining_percent, "N/A",
                    r_init_time,  # <-- Valore Routing Init Time
                    r_end_time,  # <-- Valore Routing End Time
                    r_duration  # <-- Valore Routing Duration
                ])

            # rejected tasks
            # Calcola la percentuale di energia rimanente per i task rifiutati
            remaining_percent = "N/A"
            if isinstance(srv.energy, (int, float)) and initial_energy_for_percent > 0:
                remaining_percent = (srv.energy / initial_energy_for_percent) * 100

            for (tid, task_type, arr_sys, img_size, reason, d_cpu) in getattr(srv, 'rejected_tasks', []):
                # --- INIZIO BLOCCO CORRETTO PER REJECTED ---
                writer.writerow([
                    tid,  # Task ID
                    task_type,  # Task Type
                    "Rejected",  # Status
                    arr_sys,  # Arrival Time (System)
                    "N/A",  # Arrival Time (Queue)
                    "N/A",  # Start Time
                    "N/A",  # End Time
                    d_cpu,  # Execution time (Corretto)
                    0.0,  # Service Time (Corretto)
                    0.0,  # Time in system (Corretto)
                    0.0,  # Time in queue (Corretto)
                    srv.name,  # Server Name
                    0,  # Num Hops (Corretto)
                    "N/A",  # Num Hops Routing
                    "N/A",  # Queue length
                    0.0,  # transfer_time (Corretto)
                    img_size,  # Image_Size_MB
                    False,  # Exec_after_set (Corretto)
                    0.0,  # Energy_CPU [J] (Corretto)
                    0.0,  # Energy_NET [J] (Corretto)
                    0.0,  # Energy_TOTAL [J] (Corretto)
                    srv.energy,  # Remaining_energy [J]
                    remaining_percent,  # Remaining_energy [%]
                    reason,  # Rejection Reason
                    "N/A",  # Routing Init Time
                    "N/A",  # Routing End Time
                    "N/A"  # Routing Duration
                ])

            # residual tasks: CPU queue & NET queue
            residual_tasks = []
            if ENABLE_MONITORING:  # Controlla solo se il monitoring è attivo
                for req in getattr(srv, 'cpu_dev').queue:
                    if hasattr(req, 'task_data'):
                        td = req.task_data
                        residual_tasks.append(
                            {"tid": td.id, "type": "CPU_Waiting", "demand": getattr(td, 'd_cpu', 'N/A')})
                for req in getattr(srv, 'net_dev').queue:
                    if hasattr(req, 'task_data'):
                        td = req.task_data
                        residual_tasks.append(
                            {"tid": td.id, "type": "NET_Waiting", "demand": getattr(td, 'd_net', 'N/A')})

            total_residual_count = len(residual_tasks)
            # Calcola la percentuale rimanente anche per i task residui (usa la stessa di rejected)
            remaining_percent = "N/A"
            if isinstance(srv.energy, (int, float)) and initial_energy_for_percent > 0:
                remaining_percent = (srv.energy / initial_energy_for_percent) * 100

            for task_data in residual_tasks:
                tid = task_data["tid"]
                task_type_label = f"Residual ({task_data['type']})"
                demand = task_data["demand"]
                # Se la domanda è nota, usala, altrimenti 0.0
                execution_time = demand if task_data["type"] == "CPU_Waiting" and isinstance(demand,
                                                                                             (int, float)) else 0.0
                service_time = demand if task_data["type"] == "NET_Waiting" and isinstance(demand,
                                                                                           (int, float)) else 0.0

                # --- INIZIO BLOCCO CORRETTO PER RESIDUAL ---
                writer.writerow([
                    tid,  # Task ID
                    task_type_label,  # Task Type
                    "In Queue",  # Status
                    "N/A",  # Arrival Time (System)
                    "N/A",  # Arrival Time (Queue)
                    "N/A",  # Start Time
                    "N/A",  # End Time
                    execution_time,  # Execution time (dal demand)
                    service_time,  # Service Time (dal demand)
                    "N/A",  # Time in system
                    "N/A",  # Time in queue
                    srv.name,  # Server Name
                    "N/A",  # Num Hops
                    "N/A",  # Num Hops Routing
                    total_residual_count,  # Queue length
                    "N/A",  # transfer_time (Corretto)
                    "N/A",  # Image_Size_MB (Corretto)
                    False,  # Exec_after_set (Corretto)
                    0.0,  # Energy_CPU [J] (Corretto)
                    0.0,  # Energy_NET [J] (Corretto)
                    0.0,  # Energy_TOTAL [J] (Corretto)
                    srv.energy,  # Remaining_energy [J]
                    remaining_percent,  # Remaining_energy [%]
                    "In Queue at End",  # Rejection Reason
                    "N/A",  # Routing Init Time
                    "N/A",  # Routing End Time
                    "N/A"  # Routing Duration
                ])

        # dump also global batch completions (if any)
        # NOTA: Ora che usiamo TaskAssignment_ILP, i batch vengono registrati anche in srv.completed_tasks.
        # Tuttavia, TaskAssignment_ILP popola anche globals.gbl_tasks.
        # Se TaskAssignment_ILP non popola globals.gbl_batch_completed, questa parte sarà vuota,
        # ma i dati saranno corretti sopra (sotto srv.completed_tasks).
        for entry in getattr(globals, 'gbl_batch_completed', []):
            (tid, task_type, arr_sys, arr_q, start_t, end_t, ex_t, service_t,
             tq, sel_srv, hops, qlen, trf, image_size,
             exec_set, eps_cpu, eps_net, eps_tot, srv_rem_energy) = entry

            time_in_system = (end_t - arr_sys) if (
                    isinstance(end_t, (int, float)) and isinstance(arr_sys, (int, float))) else "N/A"

            remaining_percent = "N/A"
            if isinstance(srv_rem_energy, (int, float)) and initial_energy_for_percent > 0:
                remaining_percent = (srv_rem_energy / initial_energy_for_percent) * 100

            writer.writerow([
                tid, task_type, "Completed", arr_sys, arr_q, start_t, end_t,
                ex_t, service_t, time_in_system, tq, sel_srv, hops,
                "N/A",  # Num Hops Routing
                qlen, trf, image_size,
                exec_set, eps_cpu, eps_net, eps_tot, srv_rem_energy, remaining_percent, "N/A",
                "N/A",  # Routing Init Time
                "N/A",  # Routing End Time
                "N/A"  # Routing Duration
            ])

    print(f"Simulation results saved to: {csv_task}")

    # --------------------------------------------------
    # 8) Scrittura Statistiche Energetiche e Task Counts
    # --------------------------------------------------

    csv_energy_stats = (
        f"{base_dir}/energy_stats_"
        f"{gen_dist}_REQ-{complete_sim_solver}_"
        f"AT_{atime}_CPU_{cpu_mean}.csv"
    )

    print(f"\nSalvataggio statistiche energetiche e conteggi task su: {csv_energy_stats}")

    try:
        # Prendi l'energia iniziale dal config per calcolare la percentuale
        initial_energy = config.get("initial_energy", 0.0)

        # 1. Inizializza un dizionario per raccogliere le statistiche
        stats_per_server = {}
        for srv in all_servers:
            stats_per_server[srv.name] = {
                "server_obj": srv,
                "energy_consumptions": [],
                "task_counts": {
                    "Generic_Service": 0,
                    "CPU_Intensive": 0,
                    "CPU_and_Data_Intensive": 0,
                    "Batch": 0
                }
            }

        # 2. Popola le statistiche dai task NON-BATCH (da srv.completed_tasks)
        for srv in all_servers:
            for entry in getattr(srv, 'completed_tasks', []):
                task_type = entry[1]  # Indice 1 per task_type
                energy = entry[18]  # Indice 18 per eps_tot

                if task_type in stats_per_server[srv.name]["task_counts"]:
                    stats_per_server[srv.name]["task_counts"][task_type] += 1

                if isinstance(energy, (int, float)) and energy > 0.0:
                    stats_per_server[srv.name]["energy_consumptions"].append(energy)

        # 3. Popola le statistiche dai task BATCH (da globals.gbl_batch_completed)
        for entry in getattr(globals, 'gbl_batch_completed', []):
            server_name = entry[9]  # Indice 9 per sel_srv
            task_type = entry[1]  # Indice 1 per task_type
            energy = entry[18]  # Indice 18 per eps_tot

            if server_name in stats_per_server and task_type == "Batch":
                stats_per_server[server_name]["task_counts"]["Batch"] += 1
                if isinstance(energy, (int, float)) and energy > 0.0:
                    stats_per_server[server_name]["energy_consumptions"].append(energy)

        # 4. Scrivi il file CSV
        with open(csv_energy_stats, mode='w', newline='') as f_stats:
            writer_stats = csv.writer(f_stats)

            # Scrivi l'intestazione
            writer_stats.writerow([
                "Server_Name",
                "Total_Tasks_Completed",
                "Generic_Service_Count",
                "CPU_Intensive_Count",
                "CPU_Data_Intensive_Count",
                "Batch_Count",
                "Generic_Service_%",
                "CPU_Intensive_%",
                "CPU_Data_Intensive_%",
                "Batch_%",
                "Total_Energy_Consumed_J",
                "Total_Energy_Consumed_%",
                "Min_Energy_Consumed_J",
                "Max_Energy_Consumed_J",
                "Avg_Energy_Consumed_J",
                "Final_Remaining_Energy_J"
            ])

            # 5. Calcola le statistiche finali e scrivi le righe
            for server_name, stats in stats_per_server.items():

                task_counts = stats["task_counts"]
                energy_consumptions = stats["energy_consumptions"]

                total_tasks_completed = sum(task_counts.values())

                # Calcolo percentuali per tipo di task
                if total_tasks_completed > 0:
                    percent_generic = (task_counts["Generic_Service"] / total_tasks_completed) * 100
                    percent_cpu = (task_counts["CPU_Intensive"] / total_tasks_completed) * 100
                    percent_cpu_data = (task_counts["CPU_and_Data_Intensive"] / total_tasks_completed) * 100
                    percent_batch = (task_counts["Batch"] / total_tasks_completed) * 100
                else:
                    percent_generic = 0.0
                    percent_cpu = 0.0
                    percent_cpu_data = 0.0
                    percent_batch = 0.0

                # Calcolo statistiche energetiche
                if energy_consumptions:
                    total_e = sum(energy_consumptions)
                    min_e = min(energy_consumptions)
                    max_e = max(energy_consumptions)
                    avg_e = total_e / len(energy_consumptions)
                    total_e_percent = (total_e / initial_energy) * 100 if initial_energy > 0 else 0.0
                else:
                    total_e = 0.0
                    min_e = 0.0
                    max_e = 0.0
                    avg_e = 0.0
                    total_e_percent = 0.0

                # Scrivi la riga
                writer_stats.writerow([
                    server_name,
                    total_tasks_completed,
                    task_counts["Generic_Service"],
                    task_counts["CPU_Intensive"],
                    task_counts["CPU_and_Data_Intensive"],
                    task_counts["Batch"],
                    percent_generic,
                    percent_cpu,
                    percent_cpu_data,
                    percent_batch,
                    total_e,
                    total_e_percent,
                    min_e,
                    max_e,
                    avg_e,
                    stats["server_obj"].energy  # Energia finale rimasta
                ])

    except Exception as e:
        print(f"ERRORE during writing the energy stats file: {e}")

    # Calcolo tempo reale di esecuzione
    end_time_simulation_real = time.time()
    duration_simulation = end_time_simulation_real - start_time_simulation_real
    print(f"Tempo di esecuzione REALE della simulazione {duration_simulation:.4f} secondi")