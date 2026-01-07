"""
EdgeServer Module
=================

This module defines the `EdgeServer` class, which represents a Low Earth Orbit (LEO) satellite
acting as an edge computing node within the simulation environment.

The module integrates discrete event simulation (SimPy) with orbital mechanics (Skyfield)
to simulate a dynamic satellite network.

Key Functionalities:
--------------------
* **Dual-Stage Resource Model**: Manages separate queues for CPU (computation) and Network (transmission).
* **Routing Logic**: Implements decisions for various routing algorithms (BATMAN, GREEDY, DSR)
    and handles packet forwarding based on orbital topology.
* **Energy Management**: Simulates energy consumption for processing and transmission,
    enforcing energy budgets and handling battery depletion.
* **Metric Estimation**: Provides methods to estimate Waiting Times (W_cpu, W_net)
    and calculate Selection Scores for task offloading decisions.

Global Configuration:
---------------------
The module relies on a global configuration dictionary (`globals.config`) to set parameters
such as CPU capacity, initial energy, and routing flags.
"""
import globals
from math import sqrt
import simpy
from skyfield.api import EarthSatellite
from collections import OrderedDict
from user_based_topology import getSystemFromSat
from Task import Task, findAlgorithm
from utils import colorize, sendTask


# Leggi il file di configurazione JSON
config = globals.config

BATMAN = globals.config["Routing_algorithm"]["BATMAN"]
GREEDY = globals.config["Routing_algorithm"]["GREEDY"]
DSR = globals.config["Routing_algorithm"]["DSR"]

class EdgeServer:
    def __init__(self, env, name, satellite: EarthSatellite, orbitalSunset, is_acc_point, elev_angle):
        """
        Initialize an EdgeServer instance.

        :param env: Simulation environment.
        :param name: Name of the edge server.
        """

        self.env = env
        self.name = name
        self.satellite = satellite
        self.orbitalSunset = orbitalSunset
        self.is_acc_point = is_acc_point
        self.elev_angle = elev_angle
        self.neighbors = {}
        self.latency = {}
        self.bandwidth = {}
        self.completed_tasks = []
        self.cpu_busy_until = 0.0
        self.net_busy_until = 0.0

        # Nuove code CPU + NET per modello a 2 stadi
        cpu_capacity = config.get("cpu_capacity_queue", 5)
        net_capacity = config.get("net_capacity_queue", 5)
        self.cpu_dev = simpy.Resource(env, capacity=cpu_capacity)
        self.net_dev = simpy.Resource(env, capacity=net_capacity)
        self._cpu_capacity = cpu_capacity
        self._net_capacity = net_capacity

        self.energy_reserved = 0.0

        self.rejected_tasks = []  # Lista per i task scartati
        self.energy = config.get("initial_energy", 10000.0)  # J (valore più alto)

        C_sen_cfg = config.get("C_sen", {"min": 1e7, "max": 1e10})
        self.C_sen_min = C_sen_cfg.get("min", 1e7)
        self.C_sen_max = C_sen_cfg.get("max", 1e10)

        self.C_sen = globals.rnd.uniform(self.C_sen_min, self.C_sen_max)

        self.tasks = []  # Lista task da Spedire
        self.dead_tasks = []  # Lista dei Task Morti (TTL = 0)

        self.ogm_sequence = 0
        self.OGMs = []
        self.OGMs_NP = []
        self.ogm_table = {}
        self.OGMs_History = OrderedDict()
        self.OGMs_History_dim = 2046
        self.ogm_sequence = 0           # Contatore OGM emessi
        self.OGMs = []                  # OGM to process
        self.OGMs_NP = []               # OGM received and Not-Processed
        self.ogm_table = {}             # OGMs Table {'originator': { 'neighbor': 'count'

        self.OGMs_History = OrderedDict()# Lista OGM visionati in passato (FIFO)
        self.OGMs_History_dim = 2046     # Limite dimensione History OGM

        self.routes = {}            # Dizionario di Percorsi arrivati
        self.packets = []           # Lista di pacchetti da smaltire
        self.packets_seq = 0        # contatore pacchetti spediti
        self.pkt_history = []

    def export_state(self, env):
        """
        Esporta lo stato corrente del satellite come dizionario, includendo i task
        in coda (.queue) e quelli attualmente in servizio (.users).
        """
        ENABLE_MONITORING = config.get("enable_queue_monitoring", False)

        # Funzione helper per raccogliere i dati da una lista di richieste SimPy
        def get_tasks_data(resource_list, demand_type):
            tasks_list = []
            for req_or_user in resource_list:
                # Se è una richiesta, potrebbe essere incapsulata in un wrapper SimPy
                # In questo setup, assumiamo che l'oggetto request abbia task_data
                if hasattr(req_or_user, 'task_data'):
                    task_obj = req_or_user.task_data
                    tasks_list.append({
                        "task_id": task_obj.id,
                        # Usa il tipo di domanda appropriato (d_cpu o d_net)
                        "demand": getattr(task_obj, demand_type, 'N/A'),
                        "deadline": getattr(task_obj, 'deadline', 'N/A'),
                        "image_size_MB": getattr(task_obj, 'image_size_MB', 'N/A'),
                    })
            return tasks_list

        tasks_in_cpu_queue = []
        tasks_in_net_queue = []
        tasks_in_cpu_service = []
        tasks_in_net_service = []

        if ENABLE_MONITORING:
            # Code (In attesa di iniziare)
            tasks_in_cpu_queue = get_tasks_data(self.cpu_dev.queue, 'd_cpu')
            tasks_in_net_queue = get_tasks_data(self.net_dev.queue, 'd_net')

            # Servizio (In esecuzione)
            # Dobbiamo considerare i task che stanno usando la risorsa in questo istante
            tasks_in_cpu_service = get_tasks_data(self.cpu_dev.users, 'd_cpu')
            tasks_in_net_service = get_tasks_data(self.net_dev.users, 'd_net')

        # --- Raccolta Dettagli Vicini (mantenuti dalla versione precedente) ---
        neighbor_data = []
        for neighbor_obj in self.get_neighbors():
            neighbor_name = neighbor_obj.name
            neighbor_data.append({
                "name": neighbor_name,
                "latency_s": self.latency.get(neighbor_obj, 'N/A'),
                "bandwidth_MBps": self.bandwidth.get(neighbor_obj, 'N/A'),
                "elev_angle": neighbor_obj.elev_angle,
            })

        # --- Assemblaggio dello Stato Completo ---
        state = {
            "time": env.now,
            "satellite": self.name,
            "in_listening_dome": self.elev_angle >= 40,
            "elev_angle": self.elev_angle,
            "energy_budget_J": self.energy,
            "energy_reserved_J": self.energy_reserved,
            "is_access_point": self.is_acc_point,
            "position": self.getPositionVector(globals.ist_in_conf),
            
            # Task Completati
            "completed_tasks_count": len(self.completed_tasks),
            # Code e Task
            "queue_cpu_len": len(tasks_in_cpu_queue),  # Conteggio solo i task in attesa
            "queue_net_len": len(tasks_in_net_queue),  # Conteggio solo i task in attesa

            # Nuovi campi per monitoraggio
            "cpu_service_len": len(tasks_in_cpu_service),
            "net_service_len": len(tasks_in_net_service),

            "queue_cpu_waiting": tasks_in_cpu_queue,
            "queue_net_waiting": tasks_in_net_queue,
            "queue_cpu_service": tasks_in_cpu_service,
            "queue_net_service": tasks_in_net_service,

            # Vicini (dettagliati)
            "neighbors_count": len(neighbor_data),
            "neighbors_metrics": neighbor_data,
        }

        if ENABLE_MONITORING:
            return state
        return None
    def record_rejected_task(self, task_id, task_type, arrival_time_system, img_size, rejection_reason, d_cpu ):
        """
        Registra un task scartato con la motivazione del rifiuto.
        """
        self.rejected_tasks.append(
            (task_id, task_type, arrival_time_system, img_size, rejection_reason, d_cpu )
        )
        print(f"[Task {task_id}] REJECTED on {self.name} due to: {rejection_reason}")

    def task_completed(self, task_id, task_type, arrival_time_system, arrival_time_task_queue,
                       start_time, end_time, execution_time, service_time, time_in_queue,
                       selected_server, num_hops, lunghezza_coda, transfer_time, image_size,
                       DeadLine, exec_after_set,
                       eps_cpu=0.0, eps_net=0.0):
        '''
        Record completed tasks, including energy metrics (CPU + NET) and task type.
        '''

        # Calcola l'energia totale all'inizio della funzione per evitare l'errore
        total_energy = eps_cpu + eps_net

        if self.elev_angle < config["Phi_max"]:
            exec_after_set = True

        self.completed_tasks.append(
            (task_id,  task_type, arrival_time_system, arrival_time_task_queue,
             start_time, end_time, execution_time, service_time, time_in_queue,
             selected_server, num_hops, lunghezza_coda,
             transfer_time, image_size,
             DeadLine, exec_after_set, eps_cpu, eps_net, total_energy, self.energy)
        )

        print(f"[Task {task_id}] Task eseguito su {self.name} {self.elev_angle} degrees | "
              f"CPU={eps_cpu:.4f}J, NET={eps_net:.4f}J, TOTAL={total_energy:.4f}J, "
              f"Remaining={self.energy:.2f}J")

    def add_neighbor(self, neighbor_server, hop_count, latency, bandwidth):
        '''
                Add a neighbor server with its hop count and latency.

                :param neighbor_server: Neighbor server to add.
                :param hop_count: Number of hops to reach the neighbor server.
                :param latency: Latency to the neighbor server.

                :return: None
                '''
        self.neighbors[neighbor_server] = hop_count
        self.latency[neighbor_server] = latency
        self.bandwidth[neighbor_server] = bandwidth

    def update_neighbors(self, new_neighbors, new_latency, new_bandwidth):
        """
        Update the neighbors, latency, and bandwidth of the edge server.

        :param new_neighbors: Dictionary of new neighbors and their hop counts.
        :param new_latency: Dictionary of new latencies to the neighbors.
        :param new_bandwidth: Dictionary of new bandwidths to the neighbors.

        :return: None
        """
        self.neighbors = new_neighbors
        self.latency = new_latency
        self.bandwidth = new_bandwidth

    def get_neighbors(self):
        '''
        Get neighbor servers at a distance of one hop.

        :return: List of neighbor servers at a distance of one hop.
        '''
        neighbors_at_distance_one = []

        for neighbor, hop_count in self.neighbors.items():
            if hop_count <= 1:
                neighbors_at_distance_one.append(neighbor)

        return neighbors_at_distance_one

    def get_satellite(self):
        return self.satellite

    def get_latency(self, neighbor_server):
        '''
        Get latency to a specific neighbor server.

        :param neighbor_server: Neighbor server to get the latency for.

        :return: Latency to the neighbor server.
        '''
        return self.latency.get(neighbor_server, None)

    def get_bandwidth(self, neighbor_server):
        '''
        Get bandwidth to a specific neighbor server.

        :param neighbor_server: Neighbor server to get the bandwidth for.

        :return: Bandwidth to the neighbor server.
        '''
        return self.bandwidth.get(neighbor_server, None)

    def __str__(self):
        return f"Satellite :{self.name}\n\telev:{self.elev_angle}\n\tis_AP:{self.is_acc_point}"

    def getPositionVector(self, t):
        """
        Questa funzione ritorna un vettore in 3 dimensioni,
        rappresenta la posizione del satellite in un determinato istante.
        """

        return getSystemFromSat(self.satellite, t, True).position.km.tolist()
    # --------------------------------------------------
    # Gestione Energetica
    # --------------------------------------------------
    def compute_routing_energy(self, file_size, bandwidth, Ptrasm=1.0):
        """
        Energia di routing (trasmissione) [Joule].
        file_size in Byte, bandwidth in Byte/s
        """
        if bandwidth and bandwidth > 0:
            return Ptrasm * (file_size / bandwidth)
        return 0.0

    def compute_execution_energy(self, execution_time, C_sen, e=5e-26):
        """
        Energia di computazione [Joule].
        execution_time ~ domanda di servizio (s)
        C_sen ~ capacità CPU in cicli/s
        """
        d = execution_time
        return d * e * (C_sen ** 3)

    # --------------------------------------------------
    # Waiting time (W^cpu, W^net)
    # --------------------------------------------------

    def _get_min_remaining(self, resource, kind='cpu'):
        """
        Ritorna il tempo minimo residuo tra i task in servizio (tempo fino al primo rilascio).
        Se ci sono slot liberi, ritorna 0.0.
        """
        now = self.env.now
        if len(resource.users) < resource.capacity:
            return 0.0

        min_remaining = float('inf')
        for user in resource.users:
            if hasattr(user, 'task_data'):
                td = user.task_data
                if kind == 'cpu':
                    d = getattr(td, 'd_cpu', None)
                    start = getattr(td, 'start_time_cpu', None)
                    legacy_busy_until = getattr(self, 'cpu_busy_until', None)
                else:
                    d = getattr(td, 'd_net', None)
                    start = getattr(td, 'start_time_net', None)
                    legacy_busy_until = getattr(self, 'net_busy_until', None)

                if d is None:
                    continue

                if start is not None:
                    remaining = max(0.0, d - (now - start))
                else:
                    # fallback su busy_until se disponibile, altrimenti assumiamo d come upper bound
                    if legacy_busy_until is not None:
                        remaining = max(0.0, legacy_busy_until - now)
                    else:
                        remaining = d

                min_remaining = min(min_remaining, remaining)

        return min_remaining if min_remaining != float('inf') else 0.0

    def _get_running_remaining_total(self, resource, kind='cpu'):
        """
        Somma del tempo residuo dei task attualmente in esecuzione (tutti i server).
        Serve per calcolare il lavoro totale in corso.
        """
        now = self.env.now
        running_sum = 0.0
        for user in resource.users:
            if hasattr(user, 'task_data'):
                td = user.task_data
                if kind == 'cpu':
                    d = getattr(td, 'd_cpu', None)
                    start = getattr(td, 'start_time_cpu', None)
                    legacy_busy_until = getattr(self, 'cpu_busy_until', None)
                else:
                    d = getattr(td, 'd_net', None)
                    start = getattr(td, 'start_time_net', None)
                    legacy_busy_until = getattr(self, 'net_busy_until', None)

                if d is None:
                    continue

                if start is not None:
                    remaining = max(0.0, d - (now - start))
                else:
                    if legacy_busy_until is not None:
                        remaining = max(0.0, legacy_busy_until - now)
                    else:
                        remaining = 0.5 * d  # fallback conservativo

                running_sum += remaining

        return running_sum

    def W_cpu(self):
        """
        Stima il tempo di attesa (tempo residuo in coda) per un nuovo task
        nella coda CPU (W_cpu). Utilizza il principio del lavoro totale
        residuo diviso per la capacità di servizio (tempo di servizio medio).
        """

        # 1. Calcola la somma dei d_cpu di tutti i task attualmente in coda
        queue_sum = self._get_queue_demand_sum(self.cpu_dev.queue, kind='cpu')

        # 2. Ottiene il tempo residuo minimo di esecuzione del task attualmente servito.
        #    Questo è il tempo che manca al completamento del task in servizio che ha la domanda minima.
        #    Se la CPU è libera, sarà 0.0.
        min_rem = self._get_min_remaining(self.cpu_dev, kind='cpu')

        # 3. Ottiene la somma del tempo residuo di tutti i task attualmente in servizio.
        #    Questo include il tempo del task che definisce min_rem più gli altri task in servizio.
        running_total = self._get_running_remaining_total(self.cpu_dev, kind='cpu')

        # 4. Determina la capacità del server (numero di CPU)
        capacity = max(1, getattr(self, '_cpu_capacity', 1))

        # 5. Calcola il lavoro totale da completare prima che il nuovo task sia servito.
        #    (Lavoro residuo sui task in esecuzione + Lavoro dei task in attesa)
        work_tot = running_total + queue_sum

        # 6. Lavoro residuo dopo il completamento del primo intervallo (min_rem).
        #    Durante l'intervallo min_rem, 'capacity' unità di lavoro (i server)
        #    sono processate, consumando (capacity * min_rem) lavoro totale.
        #    work_after_first è il lavoro residuo che deve essere gestito dopo che
        #    almeno un server si è liberato.
        work_after_first = max(0.0, work_tot - capacity * min_rem)

        # 7. Stima finale del tempo di attesa (W_cpu):
        #    tempo di completamento del task più vicino (min_rem)
        #    + tempo per processare il lavoro restante (work_after_first / capacity,
        #      poiché ora 'capacity' server gestiranno il resto del lavoro).
        return min_rem + (work_after_first / capacity)

    def W_net(self):
        """
        Stima il tempo di attesa (tempo residuo in coda) per un nuovo task
        nella coda Network (W_net). La logica è identica a W_cpu, ma applicata
        alla risorsa di rete (net_dev).
        """

        # 1. Calcola la somma dei d_net di tutti i task attualmente in coda
        queue_sum = self._get_queue_demand_sum(self.net_dev.queue, kind='net')

        # 2. Ottiene il tempo residuo minimo di esecuzione del task attualmente servito
        min_rem = self._get_min_remaining(self.net_dev, kind='net')

        # 3. Ottiene la somma del tempo residuo di tutti i task attualmente in servizio
        running_total = self._get_running_remaining_total(self.net_dev, kind='net')

        # 4. Determina la capacità del server di rete (di solito 1)
        capacity = max(1, getattr(self, '_net_capacity', 1))

        # 5. Calcola il lavoro totale da completare prima che il nuovo task sia servito.
        work_tot = running_total + queue_sum

        # 6. Lavoro residuo dopo il completamento del primo intervallo (min_rem).
        #    work_tot - (capacity * min_rem) è la quantità di lavoro residuo
        #    che i 'capacity' server devono ancora processare.
        work_after_first = max(0.0, work_tot - capacity * min_rem)

        # 7. Stima finale del tempo di attesa (W_net):
        #    (tempo di completamento del task più vicino) + (tempo per processare il lavoro restante)
        return min_rem + (work_after_first / capacity)

    def _get_queue_demand_sum(self, simpy_resource_queue, kind='cpu'):
        total_demand = 0.0
        for req in simpy_resource_queue:
            if hasattr(req, 'task_data'):
                task_obj = req.task_data
                if kind == 'cpu':
                    total_demand += getattr(task_obj, 'd_cpu', 0.0)
                else:
                    total_demand += getattr(task_obj, 'd_net', 0.0)
        return total_demand

    # --------------------------------------------------
    # Pipeline CPU -> NET
    # --------------------------------------------------

    def greedy_approach(self, env, task):
        dest_pos = globals.observer.getPositionVector(globals.ist_in_conf)
        ranker_neighbors = []

        for server in self.neighbors:
            neighbor_distance = get_pos_proximity(dest_pos, server.getPositionVector(globals.ist_in_conf))
            t = (server, neighbor_distance, server.is_acc_point)
            ranker_neighbors.append(t)

        ranker_neighbors.sort(key=lambda x: (not x[2], x[1]))

        best_server = None
        for neighbor_tuple in ranker_neighbors:
            if neighbor_tuple[0].name not in task.visited:
                best_server = neighbor_tuple[0]
                break

        if best_server:
            yield from sendTask(env, task, self, best_server, 'GREEDY')

    def deliver_to_Observer(self, env, mode, task):
        print(f"[MODE: {mode}]")
        task.routingEndTime = env.now
        algo = findAlgorithm()
        yield from sendTask(env, task, self, globals.observer, algo)

    def forward_packet(self, env):
        if len(self.neighbors) > 0:
            for task in self.tasks:

                if not task.arrived:

                    if config["AP_routing_bidirectional"]:
                        # Bidirezionale, mandiamo il task verso gli access Point
                        if self.is_acc_point:
                            yield from self.deliver_to_Observer(env, 'BIDIRECTIONAL', task)
                            continue
                    else:
                        # Controllo che il satellite sia nella Dome
                        if self.elev_angle >= 40:
                            yield from self.deliver_to_Observer(env, 'MONODIRECTIONAL', task)
                            continue

                    # ! Algorithm
                    max_neighbor = None
                    if BATMAN:
                        if task.dest_node in self.ogm_table:
                            max_neighbor, max_value = find_OGM_intersection(
                                self.ogm_table[task.dest_node], self.neighbors, task
                            )
                        else:
                            print(f"[{task.id}] {task.dest_node} Route temporaneamente Sconosciuta")                   
                    if BATMAN and GREEDY:
                        if max_neighbor:
                            yield from sendTask(env, task, self, max_neighbor, 'BATMAN')
                        else:
                            yield from self.greedy_approach(env, task)

                    elif BATMAN:
                        if max_neighbor:
                            yield from sendTask(env, task, self, max_neighbor, 'BATMAN')

                    elif GREEDY:
                        yield from self.greedy_approach(env, task)

        # else:
        # print(f"{self.name} NON HA PIù VICINI AI QUALI TRASMETTERE elev: {self.elev_angle}°")
        # print("Task IDs:", [task.id for task in self.tasks])

    def get_selection_score(self, task_type, d_cpu, d_net, D_r,
                            energy_budget_max=1.0,
                            file_size_bytes=None, bandwidth_Bps=None,
                            w_e=None, w_R=None):
        """
        Calcola lo score.
        - d_cpu: tempo CPU richiesto (s)
        - d_net: tempo rete stimato (s) (usato nel R_pred). Se vuoi eps_net corretto, passa file_size_bytes e bandwidth_Bps.
        - D_r: deadline del task (s)
        - file_size_bytes, bandwidth_Bps: opzionali, necessari per compute_routing_energy
        - w_e, w_R: pesi per energia/ritardo (se None prendono valori da config o default 0.5/0.5)
        """

        # pesi (fallback)
        if w_e is None or w_R is None:
            w_e = config.get("weight_energy", 0.5)
            w_R = config.get("weight_response", 0.5)
        # 1) predizione tempi
        Wc = self.W_cpu()
        Wn = self.W_net()
        R_predicted = Wc + d_cpu + Wn + d_net

        # 2) stima energia CPU (richiede C_sen)
        C_sen = getattr(self, 'C_sen', globals.rnd.uniform(self.C_sen_min, self.C_sen_max))


        eps_cpu = self.compute_execution_energy(d_cpu, C_sen, e=config.get("energy_coefficient", 5e-26))

        # 3) stima energia NET: richiede file_size_bytes e bandwidth_Bps
        if file_size_bytes is None or bandwidth_Bps is None:
            # se non forniti, cerca di ricavarli (o metti eps_net=0 come fallback)
            eps_net = 0.0
        else:
            eps_net = self.compute_routing_energy(file_size_bytes, bandwidth_Bps, config.get("Ptrasm", 1.0))

        # 4) verifica vincoli (deadline e energia disponibile)
        if R_predicted > D_r or (eps_cpu + eps_net) > self.energy:
            return -float('inf')  # non accettabile

        # 5) normalizzazione
        # Normalizziamo B_i rispetto a energy_budget_max
        if energy_budget_max > 0:
            B_normalized = self.energy / energy_budget_max
        else:
            # Se il massimo budget disponibile nella rete è 0,
            # significa che tutti sono scarichi. B_normalized è 0.
            B_normalized = 0.0

        # normalizziamo W rispetto alla deadline D_r (evita divisione per zero)
        denom = D_r if D_r > 0 else 1.0
        if task_type in ("Generic_Service", "CPU_Intensive"):
            W_norm = Wc / denom
        elif task_type == "Batch":
            W_norm = Wn / denom
        elif task_type == "CPU_and_Data_Intensive":
            W_norm = (Wc + Wn) / denom
        else:
            W_norm = 0.0

        # 6) score pesato: vogliamo massimizzare beneficio (B_normalized) e minimizzare ritardo (W_norm)
        score = w_e * B_normalized - w_R * W_norm
        return score

def find_OGM_intersection(ogm_table, neighbors, task):
    """
    Trova l'intersezione tra i vicini reali e quelli presenti nella tabella OGM,
    escludendo i satelliti già visitati dal task, e restituisce il vicino con il valore OGM più alto.

    Args:
        ogm_table (dict): Dizionario che mappa i nomi dei vicini ai valori OGM.
        neighbors (dict): Dizionario vicini.
        task (Task): Oggetto task che contiene l'insieme dei satelliti già visitati.

    Returns:
        tuple: (max_neighbor, max_value)
            - max_neighbor: Il vicino con il valore OGM più alto (oggetto neighbor).
            - max_value: Il valore OGM associato a max_neighbor.
            Se non ci sono vicini validi, entrambi sono None.
    """
    # Costruisce un dizionario di vicini che sono sia nella tabella OGM sia tra i vicini reali,
    # escludendo quelli già visitati dal task (per evitare loop).
    intersection = {
        neighbor: ogm_table[neighbor.name]
        for neighbor in neighbors
        if (
            neighbor.name in ogm_table
            and neighbor.name not in task.visited  # filtro anti-loop
        )
    }

    # Trova il vicino con il valore OGM più alto nell'intersezione.
    max_neighbor, max_value = None, None
    if intersection:  # Evita ValueError se intersection è vuoto
        max_neighbor, max_value = max(
            intersection.items(), key=lambda item: item[1])

    return max_neighbor, max_value

def get_pos_proximity(pos1, pos2):
    """
    Calcola la distanza fra due punti in uno spazio tridimensionale
    Args:
        pos1: Vettore posizionale dell'obj1
        pos2: Vettore posizionale dell'obj2
    :return: lunghezza del segmento obj1 -> obj2
    """
    # Extraction of coordinate components
    x1, y1, z1 = pos1
    x2, y2, z2 = pos2

    # Calculate the Euclidean distance
    return sqrt((x2 - x1)**2 + (y2 - y1)**2 + (z2 - z1)**2)

def build_task_csv_path(folder, at, cpu):
    csv_routing_task = ""
    if BATMAN and GREEDY:
        csv_routing_task = f"{folder}/[DINAMICO]_AT_{at}_CPU_{cpu}.csv"
    elif BATMAN:
        csv_routing_task = f"{folder}/[BATMAN]_AT_{at}_CPU_{cpu}.csv"
    elif GREEDY:
        csv_routing_task = f"{folder}/[GREEDY]_AT_{at}_CPU_{cpu}.csv"
    elif DSR:
        csv_routing_task = f"{folder}/[DSR]_AT_{at}_CPU_{cpu}.csv"
    return csv_routing_task
