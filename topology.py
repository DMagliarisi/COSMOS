import sys
import globals
import json, json5
import threading
from skyfield.api import EarthSatellite, load
from skyfield.timelib import Time
from EdgeServer import EdgeServer
from enums import AccPointMode
from user_based_topology import OBSERVER, get_orbit_proximity, get_current_time, getLatency, are_satellites_equal, getAllSatOnMe, compute_distances_from_target_satellite, create_satellite_Identity_card, advance_time, ts
from datetime import datetime, timedelta, timezone
from routing_Manager import print_dict, manage_ogm_test, saveInfoInFile
from enums import AccPointMode, GenConfigsOutput, tle_data
from utils import colorize 

# Converti il tempo in UTC e formatta
time_top = datetime.now(timezone.utc)  # O il tuo oggetto datetime

# Leggi il file di configurazione JSON
config = globals.config

# Gestione thread
lock = threading.Lock()  # Meccanismo di lock


def compute_distances_from_target_sw(sat, closerServer_Sorted, t):
    """
    Compute the distances from the target satellite to other satellites.

    :param sat: The target satellite.
    :param closerSatellite_Sorted: List of satellites sorted by proximity.
    :param t: Current time.

    :return: List of tuples containing satellites and their distances from the target satellite.
    """

    vector_Sat_Topology = []
    for i in range(0, len(closerServer_Sorted)):
        if are_satellites_equal(sat.satellite, closerServer_Sorted[i].satellite):
            pass
        else:
            proximity = get_orbit_proximity(sat.get_satellite(), closerServer_Sorted[i].get_satellite(), t)
            if proximity < config["Laser_Communication_Range"]:  # Check laser distance
                vector_Sat_Topology.append((closerServer_Sorted[i], proximity))

    sat_vector_Topology_sorted = sorted(vector_Sat_Topology, key=lambda x: x[1])
    return sat_vector_Topology_sorted


def create_topology_dome(env, time=get_current_time()):

    edge_servers = []
    acc_point, satellites_dome, satellites_buffer = getAllSatOnMe(time)
    num_sat_dome, num_sat_buffer, num_AP = len(satellites_dome), len(satellites_buffer), len(acc_point)

    print(f"TIME: {time.utc_strftime('%Y-%m-%d %H:%M:%S')}\n")
    print(
        f"Satelliti Considerati TOT: {num_sat_buffer + num_sat_dome + num_AP} AP: {num_AP} DOME: {num_sat_dome} BUFF: {num_sat_buffer} \n")
    tmp_sat = satellites_dome + satellites_buffer

    # Access Point Edge Servers
    for k in range(0, num_AP):
        server_id = f"{acc_point[k][0].name}"
        edge_server = EdgeServer(env, server_id, acc_point[k][0])
        edge_servers.append(edge_server)

    globals.global_access_point = edge_servers.copy()  # Salvo i nuovi access point globali

    # Tutti i satelliti nella cupola
    for i in range(0, num_sat_dome + num_sat_buffer):
        server_id = f"{tmp_sat[i][0].name}"
        edge_server = EdgeServer(env, server_id, tmp_sat[i][0])
        edge_servers.append(edge_server)

    # Calcola i vicini di ogni server
    for i in range(len(edge_servers)):
        current_server = edge_servers[i]
        neighbor = compute_distances_from_target_sw(current_server, edge_servers, time)
        for n in neighbor:
            # print(type(n[0]), " n -> ", n[0])
            current_server.add_neighbor(n[0], 1, getLatency(n[1]),
                                        globals.rnd.uniform(config["available_bandwidth"]["min"],
                                                       config["available_bandwidth"]["max"]))
        # print(current_server.name)
    return edge_servers


def find_satellite_events(satellite, t0):
    """
    Finds the events for a satellite between two times.
    Args:
        satellite (EarthSatellite): The satellite for which to find events.
        t0 (datetime): Reference time of start.
        t1 (datetime): The end time for finding events.

    Returns:
        dict : info about life of the satellite
    """
    t_end = ts.utc(t0.utc_datetime() + timedelta(minutes=config["Interval_future_event_prediction"]))    # End time for finding events
    t_start = ts.utc(t0.utc_datetime() - timedelta(minutes=config["Interval_past_event_prediction"]))    # Start time for finding events
    
    life = {}

    time, events = satellite.find_events(OBSERVER, t_start, t_end, altitude_degrees=config["Phi_max"])    # Find events for the satellite
    

    if len(time) == 0:
        life = {
            "AOS": None,
            "Max-El": None,
            "LOS": None,
            "life_seconds": None,
            "time_until_set_seconds": None,
        }
    else:

        if not time[0] < t0 < time[2]:  # If the satellite is not in the dome then its life is not defined
            time_until_set = 0
        else: # The satellite is in the dome
            
            time_until_set = timedelta(seconds = (time[2] - t0) * 86400).seconds
        life = {
            "AOS": time[0].utc_strftime(),          # Acquisition of the Satellite (AOS)
            "Max-El": time[1].utc_strftime(),       # Maximum Elevation            (Max-El)
            "LOS": time[2].utc_strftime(),          # Loss of Signal               (LOS)
            "life_seconds": timedelta(seconds = (time[2] - time[0]) * 86400).seconds,       # Life of the satellite in Dome
            "time_until_set_seconds": time_until_set   # Time until the satellite sets 
        }

        if time_until_set > 1000:
            print("ATTENZIONE")


    return life


def print_progress_bar(current_step, total_steps, bar_width=40, prefix="Avanzamento"):
    """
    Stampa una barra di progresso aggiornata sulla stessa riga.
    current_step: 1-based index della iterazione corrente
    total_steps: numero totale di step
    """
    if total_steps <= 0:
        return
    progress = current_step / total_steps
    filled = int(bar_width * progress)
    bar = "[" + "#" * filled + "-" * (bar_width - filled) + "]"
    percent = progress * 100
    print(f"\r{prefix}: {bar} {current_step}/{total_steps} ({percent:5.1f}%)", end="", flush=True)

def load_saved_configuration(mode:AccPointMode) -> GenConfigsOutput:
    path = f"data/configurations_AP_{mode}.json"
    configuration = None
    print(colorize(f"[LOAD] Caricamento configurazione <{mode}>...", "yellow"))
    
    try: 
        with open(path, "r") as f:
            configuration = json.load(f)
    except Exception as e:
        sys.exit(colorize(f"[LOAD] Errore durante il caricamento delle configurazioni: {e}", "red"))
    
    print(colorize(f"[LOAD] Configurazione <{mode}> caricata con successo.", "green"))
    return configuration

def build_configurations(t0: Time, tle_data : tle_data, mode: AccPointMode) -> GenConfigsOutput:

    config_interval = config["Interval_between_Configurations_in_seconds"]
    tot_config = int((config["simulation_duration"] + config["adding_time"]) / config_interval)
    
    config_path = f"data/configurations_AP_{mode}.json"
    
    configurations = genConfigs(
        t0,
        config_interval,
        tot_config,
        tle_data,
        config_path,
        mode
    )

    return configurations

def genConfigs(t0, interval, num_configs, tle_data, json_path, mode: AccPointMode) -> GenConfigsOutput:
    """
    Generates a list of configurations over a specified time period.
    Args:
        t0 (datetime, optional): The initial time for generating configurations. Defaults to the current time.
        interval (int, optional): The time interval (in seconds) between each configuration. Defaults to 2 minutes.
        num_configs (int, optional): The total number of Configurations in the building process.

    Returns:
        list: A list of configurations generated over the specified time period.
    """
    t, configs = t0, []  # Initialize time and configuration list
    num_access_point = config["access_point"]  # Number of access points
    totSecs = num_configs * interval  # Total duration in seconds
    
    print(f"[{mode}]GENERAZIONE CONFIGURAZIONI: ")
    for elapsed_time in range(0, totSecs, interval):
        step_index = elapsed_time // interval + 1
        print_progress_bar(step_index, num_configs, 40)

        configuration = []
        dome, sat_sort_buff = getAllSatOnMe(t, tle_data, mode) 
        topology = dome + sat_sort_buff

        # Gestione della serializzabilità
        for current_server in topology:

            neighbors = compute_distances_from_target_satellite(current_server, topology,
                                                               t)  # Compute distances to neighbors
            
            # ! Aggiungi la sezione vicini per ogni Satellite
            life = find_satellite_events(current_server.satellite, t)  # Find events for the satellite
            
            if current_server.is_acc_point:
                info_sat = create_satellite_Identity_card(current_server, life, neighbors, t,
                                                           True)  # Create neighbor info for access points
            else:
                info_sat = create_satellite_Identity_card(current_server, life, neighbors, t,
                                                           False)  # Create neighbor info for other satellites

            configuration.append(info_sat)  # Save this satellite's configuration


        data = {
            "time": t.utc_datetime().isoformat(),  # Current time in ISO format
            "configuration": configuration  # List of satellite configurations
        }
        configs.append(data)  # Append the configuration to the list
        t = advance_time(t, interval / 60)  # Advance time by the interval

    output = {
        "t0": t0.utc_datetime().isoformat(),  # Initial time in ISO format
        "interval": interval,  # Time interval between configurations
        "total_seconds": totSecs,  # Total duration for configurations
        "observer_position": config["simulation_location"],  # Observer's position
        "configurations": configs  # List of all configurations
    }

    # Salva il file JSON
    try:
        with open(json_path, "w") as f:
            # noinspection PyTypeChecker
            json.dump(output, f, indent=4)
        print("File saved successfully!")
    except IOError as e:
        print(f"Error saving configuration file: {e}")
    
    return output


def build_EdgeServer_from_config(env, configuration, ogm_tables = None):
    neighbors_SAT, tmp_ES, list_acc_point = {}, [], []
    #print_dict(dict_OGMs)
    for sat_info in configuration["configuration"]:
        server_id = f"{sat_info['satellite']}"
        name = sat_info["TLE-DATA"][0]["name"]
        line1 = sat_info["TLE-DATA"][0]["line1"]
        line2 = sat_info["TLE-DATA"][0]["line2"]
        life = sat_info["life"]["time_until_set_seconds"]
        acc_point = sat_info["is_access_point"]
        satellite_angle = sat_info["elev_angle"]

        if acc_point:
            neighbors_SAT[server_id] = sat_info["neighbors"]
            edge_server = EdgeServer(env, server_id, EarthSatellite(line1, line2, name, load.timescale()), life, acc_point, satellite_angle)
            if ogm_tables:
                edge_server.ogm_table = ogm_tables[name]
            tmp_ES.append(edge_server)
            list_acc_point.append(edge_server.name)
            
        else:
            neighbors_SAT[server_id] = sat_info["neighbors"]
            edge_server = EdgeServer(env, server_id, EarthSatellite(line1, line2, name, load.timescale()), life, acc_point, satellite_angle)
            if ogm_tables:
                edge_server.ogm_table = ogm_tables[name]
            tmp_ES.append(edge_server)
        
    return tmp_ES, neighbors_SAT, list_acc_point


def periodic_recall_Topology_monitor(env, data_configurations, OGMs_tables):
    while True:
        
        print("-" * 70)
        print(f"\t||TIME IN SIMULATION : (seconds:{env.now}) (minutes: {env.now // 60}) ||\n")
        print("MODIFICA CONFIGURAZIONE IN CORSO...\n")
        
        if OGMs_tables:
            new_edge_servers, new_global_access_point = loadConfiguration(env, data_configurations, OGMs_tables)
        else:
            new_edge_servers, new_global_access_point = loadConfiguration_simple(env, data_configurations)
            
        # ! Aggiorno le Globali
        with lock:
            globals.global_access_point = new_global_access_point
            globals.edge_servers = new_edge_servers
    
        
        globals.config_index += 1
        yield env.timeout(config["Interval_between_Configurations_in_seconds"])


def distribute_ogm(env, data_configuration, config_riempimento):
    
    while True:
        # Avvisa quando si è sull'ultimo elemento della configurazione
        if globals.config_index >= len(data_configuration["configurations"]) - 1:
            print(f"---!!!Raggiunto ultimo elemento di data_configuration (index {globals.config_index})")
        else:
            print(f"---Caricamento configurazione index {globals.config_index}")

        new_edge_servers, new_global_access_point = loadConfiguration_simple(env, data_configuration)  # Carica la configurazione
        
        with lock:
            globals.global_access_point = new_global_access_point
            globals.edge_servers = new_edge_servers

        with globals.lock_access_edge_servers_topology:
            ogm_map = [globals.observer] + globals.edge_servers_topology   
        
        num_redistributions = config["OGMs_redistribution"]
        time_section = config["Interval_between_Configurations_in_seconds"] / num_redistributions
        ogm_table_snapshot, position_dict = None, None
        at = timedelta(seconds=time_section)

        # 1) Fase di Redistribuzione
        for i in range(num_redistributions):
            new_instant = ts.utc(globals.ist_in_conf.utc_datetime() + at)
            at += timedelta(seconds=time_section)
                                 
            print(f"\tOGMS REDISTRIBUTION {i+1}/{num_redistributions} on : {new_instant.utc_iso(places=6)}")
            ogm_table_snapshot = manage_ogm_test(ogm_map, new_instant)
        
        # 2) Fase di Salvataggio
        if globals.config_index >= config_riempimento:
            
            ogm_table_path = f'data/OGMs_table.json'
            new_index = globals.config_index - config_riempimento
            saveInfoInFile(ogm_table_path, ogm_table_snapshot, new_index)

        # Caricamento Configurazione Successiva
        globals.config_index += 1  
        yield env.timeout(config["Interval_between_Configurations_in_seconds"])


def update_counters_dictionary(all_server, initial_server_counter, different_server_counter, other_server_counter):
    """
    Aggiunge nuovi server ai dizionari dei contatori o li inizializza.

    Args:
        edge_servers (list): Lista attuale di server.
        initial_server_counter (dict): Dizionario per il contatore iniziale dei server.
        different_server_counter (dict): Dizionario per il contatore dei server diversi.
        other_server_counter (dict): Dizionario per il contatore degli altri server.

    Returns:
        None: Aggiorna i dizionari in-place.
    """
    for server_name in all_server:
        if server_name not in initial_server_counter:
            initial_server_counter[server_name] = 0
        if server_name not in different_server_counter:
            different_server_counter[server_name] = 0
        if server_name not in other_server_counter:
            other_server_counter[server_name] = 0


def update_servers(new_servers, acc_point):
    """
    Aggiorna i server esistenti o aggiunge nuovi server se non presenti.

    Args:
        edge_servers (list): Lista di server esistenti (da mantenere).
        new_servers (list): Lista di nuovi server dalla nuova configurazione.

    Returns:
        dict: Dizionario aggiornato dei server.
    """

    # Crea un dizionario per i server esistenti basato sul nome
    old_servers = {server.name: server for server in globals.edge_servers}
    new_servers = {server.name: server for server in new_servers}
    
    # Dizionari per i risultati
    intersection = {name: server for name, server in old_servers.items() if
                    name in new_servers}  # Servers nell'intersezione
    
    #Aggiorno gli attributi dell'intersezione
    for name, server in intersection.items():
        server.elev_angle = new_servers[name].elev_angle
        server.orbitalSunset = new_servers[name].orbitalSunset

    # Aggiorno i riferimenti degli acc_point flags
    for name, server in intersection.items():
        if server.name in acc_point:
            server.is_acc_point = True
        else:
            server.is_acc_point = False

    A = {name: server for name, server in old_servers.items() if name not in new_servers}  # Server che sono tramontati
    B = {name: server for name, server in new_servers.items() if name not in old_servers}  # Server che sono appena sorti

    # Stampa i server che sono tramontati
    #for name, server in A.items():
        #print(f"Server tramontato: {name}, angle:{server.elev_angle} ,Orbital Sunset: {server.orbitalSunset} (sec)")

    # Stampa i server che sono appena sorti
    #for name, server in B.items():
        #print(f"Server appena sorto: {name}, angle:{server.elev_angle} ,Orbital Sunset: {server.orbitalSunset}")

    # Stampa i server nell'intersezione
    #for name, server in intersection.items():
        #print(f"Server aggiornato: {name}, angle:{server.elev_angle} ,Orbital Sunset: {server.orbitalSunset}")

    return intersection, A, B


def update_servers_neighbors(servers_dict, neighbors_SAT):
    """
    Updates the neighbors of each server in the servers_dict based on the provided neighbors_SAT information.
    Args:
        servers_dict (dict): A dictionary where keys are server names and values are server objects.
        neighbors_SAT (dict): A dictionary where keys are server names and values are lists of dictionaries
                              containing neighbor information with 'name' and 'latency' keys.
    Returns:
        dict: The updated servers_dict with neighbors information added to each server.
    The function performs the following steps:
    1. Constructs a dictionary (servers_updated_neighbors) to store updated neighbor information for each server.
    2. Iterates through each server in servers_dict and updates its neighbors based on neighbors_SAT.
    3. For each server, it creates dictionaries for hop_neighbors, latency, and bandwidth.
    4. Updates each server's neighbors using the update_neighbors method with the constructed dictionaries.
    """
    servers_updated_neighbors = {}  # Dizionario per i server con i vicini aggiornati

    # Costruzione del dizionario per salvare le informazioni dei server e dei loro vicini
    for k, v in servers_dict.items():
        neighbor, neighbors = {}, []
        for neighbor in neighbors_SAT[k]:
            neighbor = {
                'server': servers_dict[neighbor['name']],
                'latency': neighbor['latency']
            }
            neighbors.append(neighbor)

        servers_updated_neighbors[k] = {
            "obj": v,
            "neighbors": neighbors
        }

    # Inserisco i vicini per ogni Edge_server
    for name, server in servers_dict.items():
        hop_neighbors, latency, bandwidth = {}, {}, {}
        info = servers_updated_neighbors[name]

        for neighbor in info["neighbors"]:
            hop_neighbors[neighbor['server']] = 1
            latency[neighbor['server']] = neighbor['latency']
            bandwidth[neighbor['server']] = globals.rnd.uniform(config["available_bandwidth"]["min"],
                                                           config["available_bandwidth"]["max"])

            # Aggiungo i dizionari riguardanti i vicini ai rispettivi server
        server.update_neighbors(hop_neighbors, latency, bandwidth)

    return servers_dict
def loadConfiguration_simple(env, data_configurations):
    """
    Funzione identica a loadConfiguration ma non ha dipendenze dai file degli OGM_table e positions_Vectors.
    Costruisce le configurazioni satellitari usando solamente il file 'configurations.json'
    """
    global_access_point = []    # futuri acc_points
    if globals.config_index > 0:
        #print("#" * 30)
        configuration = data_configurations["configurations"][globals.config_index]
    
        globals.ist_in_conf = string_to_skyfield_time(configuration["time"])

        print("Aggiornato il Tempo Globale: ", globals.ist_in_conf.utc_strftime('%Y-%m-%d %H:%M:%S'))
        #print(f'Conf: {globals.config_index} | time : {configuration["time"]}')

        # Costruisci i nuovi server dalla configurazione
        new_servers, new_neighbors, acc_point = build_EdgeServer_from_config(env, configuration)

        # Aggiorna i server esistenti o aggiunge nuovi server se non presenti.
        intersection, old_edge_servers, new_edge_servers = update_servers(new_servers, acc_point)

        server = {**intersection, **new_edge_servers}
        
        update_counters_dictionary(server, globals.initial_server_counter,
                                   globals.different_server_counter, globals.other_server_counter)  # Aggiorno i dizionari dei nuovi aggiunti

        # Stampa per debug
        #print(f"Configurazione aggiornata. Totale server: {len({**intersection, **new_edge_servers, **old_edge_servers})}")

        # Aggiorno i vicini
        servers_in_dome_updated = update_servers_neighbors({**intersection, **new_edge_servers},
                                                           new_neighbors)  # Aggiorno i vicini per i server nell'intersection e i nuovi aggiunti
        # Pulisco i dizionari che riguardano i vicini dei server tramontati
        for server in old_edge_servers.values():
            server.update_neighbors({}, {}, {})
            server.elev_angle = 0

        with globals.lock_access_edge_servers_topology:
            # Salvo solo i satelliti che appartengono alla topologia
            globals.edge_servers_topology = list(servers_in_dome_updated.values())

        new_edge_servers = list(servers_in_dome_updated.values()) + list(old_edge_servers.values())

        #Trovo i nuovi acc_points
        counter_acc_found = 0
        for server in new_edge_servers:
            if counter_acc_found < config["access_point"]:
                if server.is_acc_point:
                    #print()
                    global_access_point.append(server)
                    counter_acc_found += 1
            else:
                break

        # Incrementa l'indice di configurazione
        # if globals.config_index > config["Number_of_Configurations"] - 1:
        #     print("(!) Hai finito le configurazioni")
        #     print(f"CONFIGURAZIONE elaborata {globals.config_index}")
        # else:
        #     globals.config_index += 1
        return new_edge_servers, global_access_point
    else:
        # Caricamento iniziale della configurazione
        configuration = data_configurations["configurations"][globals.config_index]
        
        # Costruisci i server iniziali
        edge_servers, neighbors_SAT, list_acc_point = build_EdgeServer_from_config(env, configuration)

        # Stampa per debug
        #print(f"Configurazione iniziale caricata. Totale server: {len(edge_servers)}")
        server_dict = {server.name: server for server in edge_servers}

        # Aggiungiamo i vicini per ogni elemento
        for server in edge_servers:
            neighbors = neighbors_SAT[server.name]

            for n in neighbors:
                neighbor_server = server_dict.get(n["name"])
                server.add_neighbor(neighbor_server, 1, n["latency"],
                                    globals.rnd.uniform(config["available_bandwidth"]["min"],
                                                   config["available_bandwidth"]["max"]))

        #Trovo i nuovi acc_points
        counter_acc_found = 0
        for server in edge_servers:
            if counter_acc_found < config["access_point"]:
                if server.is_acc_point:
                    global_access_point.append(server)
                    counter_acc_found += 1
            else:
                break
        
        # print("TOPOLOGIA ATTUALE:")
        # [print(sat.name) for sat in edge_servers]

        #globals.config_index += 1
        globals.ist_in_conf = string_to_skyfield_time(configuration["time"])
        globals.edge_servers_topology = edge_servers
        return edge_servers, global_access_point

def loadConfiguration(env, data_configurations, OGMs_tables):
    """
    Carica una configurazione dal file e aggiorna la lista edge_servers senza sostituirla completamente.

    Returns:
        None
    """
    global_access_point = []    # futuri acc_points
    if globals.config_index > 0:
        #print("#" * 30)
        configuration = data_configurations["configurations"][globals.config_index]
        OGMs_table_single_config = OGMs_tables[str(globals.config_index)]
        globals.ist_in_conf = string_to_skyfield_time(configuration["time"])

        print("Aggiornato il Tempo Globale: ", globals.ist_in_conf.utc_strftime('%Y-%m-%d %H:%M:%S'))
        #print(f'Conf: {globals.config_index} | time : {configuration["time"]}')

        # Costruisci i nuovi server dalla configurazione
        new_servers, new_neighbors, acc_point = build_EdgeServer_from_config(env,
                     configuration, OGMs_table_single_config)

        # Aggiorna i server esistenti o aggiunge nuovi server se non presenti.
        intersection, old_edge_servers, new_edge_servers = update_servers(new_servers, acc_point)

        server = {**intersection, **new_edge_servers}
        
        update_counters_dictionary(server, globals.initial_server_counter,
                                   globals.different_server_counter, globals.other_server_counter)  # Aggiorno i dizionari dei nuovi aggiunti

        # Stampa per debug
        #print(f"Configurazione aggiornata. Totale server: {len({**intersection, **new_edge_servers, **old_edge_servers})}")

        # Aggiorno i vicini
        servers_in_dome_updated = update_servers_neighbors({**intersection, **new_edge_servers},
                                                           new_neighbors)  # Aggiorno i vicini per i server nell'intersection e i nuovi aggiunti
        
        # Pulisco i dizionari che riguardano i vicini dei server tramontati
        [server.update_neighbors({}, {}, {}) for server in old_edge_servers.values()]  
        
        # ! Ridefinisco le Labels dei Tasks
        for server in old_edge_servers.values():
            if len(server.tasks)>0:
                print(f"{server.name} SERVER OUT OF BUFF")
                for t in server.tasks:
                    t.label = 'SEN_OUT_OF_BUFF'
                    print(f"\t{t.id} : {t.label}")

        with globals.lock_access_edge_servers_topology:
            # Salvo solo i satelliti che appartengono alla topologia
            globals.edge_servers_topology = list(servers_in_dome_updated.values())

        new_edge_servers = list(servers_in_dome_updated.values()) + list(old_edge_servers.values())

        #Trovo i nuovi acc_points
        counter_acc_found = 0
        for server in new_edge_servers:
            if counter_acc_found < config["access_point"]:
                if server.is_acc_point:
                    #print()
                    global_access_point.append(server)
                    counter_acc_found += 1
            else:
                break

        # Incrementa l'indice di configurazione
        # if globals.config_index > config["Number_of_Configurations"] - 1:
        #     print("(!) Hai finito le configurazioni")
        #     print(f"CONFIGURAZIONE elaborata {globals.config_index}")
        # else:
        #     globals.config_index += 1
        return new_edge_servers, global_access_point
    else:
        print(f"SIMULAZIONE INIZIATA conf {globals.config_index}")
        # Caricamento iniziale della configurazione
        configuration = data_configurations["configurations"][globals.config_index]
        OGMs_table_single_config = OGMs_tables[str(globals.config_index)]

        # Costruisci i server iniziali
        edge_servers, neighbors_SAT, list_acc_point = build_EdgeServer_from_config(env,
                     configuration, OGMs_table_single_config)

        # Stampa per debug
        #print(f"Configurazione iniziale caricata. Totale server: {len(edge_servers)}")
        server_dict = {server.name: server for server in edge_servers}

        # Aggiungiamo i vicini per ogni elemento
        for server in edge_servers:
            neighbors = neighbors_SAT[server.name]

            for n in neighbors:
                neighbor_server = server_dict.get(n["name"])
                server.add_neighbor(neighbor_server, 1, n["latency"],
                                    globals.rnd.uniform(config["available_bandwidth"]["min"],
                                                   config["available_bandwidth"]["max"]))
        #Trovo i nuovi acc_points
        counter_acc_found = 0
        for server in edge_servers:
            if counter_acc_found < config["access_point"]:
                if server.is_acc_point:
                    global_access_point.append(server)
                    counter_acc_found += 1
            else:
                break
        
        # print("TOPOLOGIA ATTUALE:")
        # [print(sat.name) for sat in edge_servers]

        #globals.config_index += 1
        globals.ist_in_conf = string_to_skyfield_time(configuration["time"])
        globals.edge_servers_topology = edge_servers
        return edge_servers, global_access_point

def updateTaskValue(data_configurations):
    index_config = 0
    lifes = []  # Lista per salvare le vite dei satelliti
    configurations = data_configurations["configurations"]   
    #print(f"Analisi {len(configurations)} configurazioni :")
    for i in range(len(configurations)):
        conf =  data_configurations["configurations"][index_config]
        #print(f"[{i}] Configuration time: {conf['time']} sat:({len(conf['configuration'])})")
        
        for satellite in conf["configuration"]:
            if satellite["life"]["life_seconds"] == None:
                pass
            else:
                if not any(satellite["satellite"] == sat["satellite"] for sat in lifes):
                    lifes.append(
                            {"satellite" : satellite["satellite"],
                            "life": satellite["life"]["life_seconds"]}
                        )
                    #print(f"satellite: { satellite['satellite']}\t|  life :{satellite['life']['life_seconds']}")
        #print(f"Incremento lifes: {len(lifes)}")
        index_config += 1
    #print("#" * 50)
    
    lifes_value = [sat["life"] for sat in lifes]
    #print(f"Average life: {sum(lifes_value) / len(lifes_value)}")
    #print(f"Max life: {max(lifes_value)}")
    #print(f"Min life: {min(lifes_value)}")
    
    return min(lifes_value), max(lifes_value), sum(lifes_value) / len(lifes_value)

def string_to_skyfield_time(time_str):
    # Converte la stringa in oggetto datetime
    dt = datetime.fromisoformat(time_str)
    ts = load.timescale()
    return ts.from_datetime(dt)


def get_global_mode() -> AccPointMode:
    ap_selection = globals.config.get("AP_selection")
    if ap_selection == "base":
        return AccPointMode.BASE
    elif ap_selection == "optimal":
        return AccPointMode.OPTIMAL
    else:
        sys.exit("Modalità di Access Point non valida nel file di configurazione.")
