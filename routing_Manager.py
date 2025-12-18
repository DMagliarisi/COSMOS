import json
from utils import colorize, sendTask
from packet import Mode, Packet
from user_based_topology import get_orbit_proximity
from Ogm import Ogm
from Observer import Observer
import globals
import sys

config = globals.config

DSR = config["Routing_algorithm"]["DSR"]
# Leggi il file di configurazione JSON (Contiene le configurazioni salvate)
# try:
#     with open("data/configurations.json", "r") as f:
#         print("Configuration file loaded.\n")
#         data_configurations = json.load(f)
# except Exception as e:
#     print(f"Error loading configuration file: {e}")


def clear_line():
    # Sposta il cursore all'inizio e sovrascrive con spazi
    sys.stdout.write('\r' + ' ' * 100 + '\r')
    sys.stdout.flush()

def print_progress_bar(i, total, bar_length=30):
    progress = int(bar_length * i / total)
    bar = '#' * progress + '-' * (bar_length - progress)
    output = f'\r\t\t[{bar}] {i}/{total}'

    sys.stdout.write(output)
    sys.stdout.flush()

    if i == total:
        # Alla fine: cancella riga e non va a capo
        clear_line()

def load_saved_OGM():
    try:
        with open(globals.DEFAULT_OGMS_TABLES, "r") as f:
            print(colorize(f"[LOAD] Caricamento OGM table...", "yellow"))
            ogm_data = json.load(f)
    except Exception as e:
        sys.exit(colorize(f"[ERROR] Caricamento OGM table fallito: {e}", "red"))
    return ogm_data

def manage_ogm_test(ogm_map, t):

    print("\t| Generazione OGM")
    # Ogni Nodo manda un OGM
    for node in ogm_map:
        #print(f"{node.name} | {node.getPositionVector(t)} | AP: {node.is_acc_point}")
        create_ogm(node, node.getPositionVector(t), node.is_acc_point)

    print("\t| Processing OGMs")
    total = len(ogm_map)

    for i, node in enumerate(ogm_map, start=1):
        #print(f"\t{node.name} : N to Processing ({len(node.OGMs)})")
        for ogm in node.OGMs:
            #! Fase di Controllo
            if ogm.id in node.OGMs_History or ogm.ttl == 0:  # Il pacchetto è stato già visionato o è scaduto
                continue    # non lo mando

            # Se il pacchetto è il mio ma mi è arrivato da qualqun altro
            if ogm.originator == node.name and ogm.sender != node.name:
                continue    #non lo mando

            # Se il pacchetto non l'ho generato io, lo salvo
            if ogm.originator != node.name and ogm.sender != node.name:
                node.OGMs_History[ogm.id] = {"originator": ogm.originator, "sender": ogm.sender}
            
            # ! Fase di salvataggio del OGM nella tabella di questo nodo
            if ogm.originator != node.name:  # Non mi salvo i pacchetti che riguardano questo server
                if ogm.originator not in node.ogm_table:
                    node.ogm_table[ogm.originator] = {}

                # Aggiorno la Table
                if ogm.sender not in node.ogm_table[ogm.originator]:
                    node.ogm_table[ogm.originator][ogm.sender] = 0
                node.ogm_table[ogm.originator][ogm.sender] += 1
                
            # ! Fase di redistribuzione
            if type(node) == Observer:
                # Sto analizzando un Observer
                for ap in globals.global_access_point:
                    ap.OGMs_NP.append(ogm.clone_for_forwarding(node.name))
            else:
                # Sto analizzando un Satellite normale
                for neighbor in node.neighbors.keys():
                    # Per ogni vicino
                    if neighbor.name == ogm.sender:    # Se è il vicino che mi ha mandato questo pacchetto non lo mando
                        continue
                    else:
                        proximity = get_orbit_proximity(node.get_satellite(), neighbor.get_satellite(), t)
                        failure_prob = transmission_failure_probability(proximity)
                        value = round(globals.rnd.uniform(0, 1), 2)
                        
                        if value > failure_prob:
                            # Spedisco il pacchetto
                            neighbor.OGMs_NP.append(ogm.clone_for_forwarding(node.name))
        node.OGMs = []

        print_progress_bar(i, total)


    # ! OGMs_NP -> OGMs
    for obj in ogm_map:
        obj.OGMs = obj.OGMs_NP.copy()
        obj.OGMs_NP = []

    # ! Pulizia della History
    print("\t| Cleaning History")
    for obj in ogm_map:
        # Calcolo quanto siamo fuori dimensione nella history ed eliminiamo i primi che sono entrati
        if type(obj) != Observer:
            out_dim = len(obj.OGMs_History) - obj.OGMs_History_dim
            if out_dim > 0:
                for i in range(out_dim):

                    # ! Pulizia OrderedDict
                    key, value_ogm_dict = obj.OGMs_History.popitem(last=False)                   # Rimuove il più vecchio
                    
                    obj.ogm_table[value_ogm_dict['originator']][value_ogm_dict['sender']] -= 1    # Puliamo la table
                    if obj.ogm_table[value_ogm_dict['originator']][value_ogm_dict['sender']] == 0:
                        del obj.ogm_table[value_ogm_dict['originator']][value_ogm_dict['sender']]

                    if len(obj.ogm_table[value_ogm_dict['originator']]) == 0:
                        del obj.ogm_table[value_ogm_dict['originator']]




    # ! Salvataggio informazioni table
    ogm_tables_snapshot = {
        satellite.name: satellite.ogm_table
        for satellite in ogm_map
        if type(satellite) != Observer
    }


    return ogm_tables_snapshot

def saveInfoInFile(file ,value_dictionary, N_config):
    try:
        with open(file, "r") as f:
            print(f"file {file} loaded.\n")
            data = json.load(f)
    except Exception as e:
        print(f"Error loading configuration file: {e}")
        data = {}

    # Scrivo la nuova configurazione
    data[N_config] = value_dictionary

    with open(file, "w") as f:
        json.dump(data, f, indent=4)

def transmission_failure_probability(distance):
    # Probabilità di fallimento cresce linearmente con la distanza
    return round(min(1.0, distance / config["Laser_Communication_Range"]), 2)


def print_dict(d, level=0):
    indent = '\t' * level

    if isinstance(d, dict):
        for key, value in d.items():
            if isinstance(value, (dict, list)):
                print(f"{indent}{key}:")
                print_dict(value, level + 1)
            else:
                print(f"{indent}{key}:\t{value}")
    elif isinstance(d, list):
        for i, item in enumerate(d):
            if isinstance(item, (dict, list)):
                print(f"{indent}[{i}]:")
                print_dict(item, level + 1)
            else:
                print(f"{indent}[{i}]:\t{item}")
    else:
        print(f"{indent}{d}")


def create_ogm(obj, origin_position_vect, is_AP):
    """
    Crea e invia un nuovo OGM (Originator Generated Message) ai nodi vicini.

    L'OGM include informazioni come TTL, numero di sequenza e un identificatore unico.
    Aggiorna il contatore di sequenza e registra l'OGM creato.

    Returns:
        Ogm: L'istanza del nuovo OGM creato.
    """

    ogm = Ogm(
        originator = obj.name,
        sender = obj.name,
        origin_position_vect = origin_position_vect,
        is_AP = is_AP,
        ttl = 15,
        sequence_number = obj.ogm_sequence
    )

    obj.ogm_sequence += 1          # Aumento la sequence del server
    obj.OGMs.append(ogm)           # Lo inserisco nella lista degli OGM da processare in questo server


def routeDiscovery(source, task, neighbors):
    """
    Questa funzione prende un task è fa partire un processo
    di route request fino alla destinazione.
    """
    task.routeRequestIst = source.env.now    # Save timestamp

    for node in neighbors:

        pkt_ID = source.name+"_"+str(source.packets_seq)
        pkt = Packet(pkt_ID, task.id, task.current_node, task.dest_node, Mode.ROUTE_DISCOVERY)

        node.packets.append(pkt)
        pkt.current_node = node.name

        source.pkt_history.append(pkt_ID)
        source.packets_seq += 1

        globals.gbl_packet.append(pkt)
        #print(f"\t[{task.id}] PKTID: {pkt_ID} ({source.name}->{node.name})\t| HISTORY: {pkt.hop_History}")
        #print(f"[{pkt_ID}] {task.current_node} --> {node.name}")

# def getNextNode(neighbors, nextHopName):

#     neighbor = next((n for n in neighbors if n.name == nextHopName), None)
#     return neighbor

def startRouteReply(source, neighbor_map, pkt: Packet):

    pkt.mode = Mode.ROUTE_REPLY
    last_node_name = pkt.node_stack.pop()
    nextHop = neighbor_map.get(last_node_name)
    
    if nextHop:
        nextHop.packets.append(pkt)
    else:
        if pkt in source.packets:
            print(f"{pkt.id} Rimosso da {source.name}")
            source.packets.remove(pkt)

def sendPkt(source, dest, pkt:Packet):
    if dest == None:
        print(colorize("[ERROR] Destinazione None nel sendPkt()", "red"))
        sys.exit("ERRORE: destination = None")

    newPkt = pkt.duplicate()
    newPkt.current_node = dest.name
    newPkt.visited.add(source.name)
    newPkt.node_stack.append(source.name)

    if pkt.mode == Mode.ROUTE_DISCOVERY:
        newPkt.hop_History.append(source.name)

    dest.packets.append(newPkt)

def forward_packet_dsr_best(env, node):
    
    # ? Gestione dei vicini
    if node.name != 'OBS':
        neighbors_list = list(node.neighbors.keys()) # Vicini Nodo Normale
        if node.is_acc_point:
            neighbors_list += [globals.observer]     # Vicini Nodo AP
    else:
        neighbors_list = globals.global_access_point # Vicini Observer

    neighbor_map = {n.name: n for n in neighbors_list}
    
    # ! Processamento dei pacchetti
    for pkt in node.packets[:]:
        # TODO : Aggiungi un controllo per evitare il Sovracarico della rete
        if pkt.mode == Mode.ROUTE_DISCOVERY:
            if pkt.id not in node.pkt_history:
                if pkt.dest == node.name:
                    pkt.hop_History.append(node.name)
                    startRouteReply(node, neighbor_map, pkt)
                else:
                    for n in neighbors_list:
                        sendPkt(node, n, pkt)
                # Nella history inserisco i DISCOVERY
                node.pkt_history.append(pkt.id) 

        elif pkt.mode == Mode.ROUTE_REPLY:
            if pkt.source == node.name:
                # ! Salviamo l'informazione che ci è giunta
                if pkt.taskID not in node.routes:
                    node.routes[pkt.taskID] = []
                if pkt.hop_History not in node.routes[pkt.taskID]:
                    node.routes[pkt.taskID].append(pkt.hop_History)
            else:
                lastNodeStack = pkt.node_stack.pop()
                nextNode = neighbor_map.get(lastNodeStack) 

                if nextNode is not None:
                    sendPkt(node, nextNode, pkt)
        node.packets.remove(pkt)
    
    # ! Route Request
    for task in node.tasks:
        if not task.routeRequestIst:
            routeDiscovery(node, task, neighbors_list)
        else:
            if node.name == task.source_DSR:
                timeout = 1             # 1s

                if env.now - task.routeRequestIst > timeout:
                    if task.id in node.routes:

                        nextHop = None
                        node.routes[task.id].sort(key=len)

                        while len(node.routes[task.id]) > 0:
                            route = node.routes[task.id][0].copy()
                            nextHopName = route.pop(0)
                            
                            nextHop = neighbor_map.get(nextHopName)

                            if nextHop is not None:
                                task.selected_route = route
                                break # Trovato, esco dal while mantenendo il valore di nextHop
                            else:
                                node.routes[task.id].pop(0)
                        
                        if nextHop is not None:
                            env.process(sendTask(env, task, node, nextHop, 'DSR'))
                        else:
                            # Nessuna Route Valida!
                            task.routeRequestIst = None
                            task.RouteReply = False
                            task.selected_route = []

                            if task.id in node.pkt_history:
                                node.pkt_history.remove(task.id)
                    else:
                        timeout += 1

            elif node.name == task.dest_node:
                node.arrived_tasks.append(task)
                # Usa una copia della lista se hai problemi di concorrenza, altrimenti ok
                node.tasks.remove(task) 
                task.routingEndTime = env.now
                print(colorize(f"[{task.id}] CONSEGNATO! ", "green"))

            else:
                nextHopName = task.selected_route.pop(0)
                # Lookup rapido
                nextHop = neighbor_map.get(nextHopName)

                if nextHop is not None:
                    env.process(sendTask(env, task, node, nextHop, 'DSR'))
                else:
                    # Route-Error
                    task.routeRequestIst = None
                    task.RouteReply = False
                    task.selected_route = []
                    task.source_DSR = node.name

# def forward_packet_DSR(env, node):

#     # ? Gestione dei vicini
#     if node.name != 'OBS':
#         neighbors = list(node.neighbors.keys()) # Vicini Nodo Normale
#         if node.is_acc_point:
#             neighbors += [globals.observer]     # Vicini Nodo AP
#     else:
#         neighbors = globals.global_access_point # Vicini Observer

#     # ! Processamento dei pacchetti
#     for pkt in node.packets[:]:
#         # TODO : Aggiungi un controllo per evitare il Sovracarico della rete
#         if pkt.mode == Mode.ROUTE_DISCOVERY:
#             if pkt.id not in node.pkt_history:
#                 if pkt.dest == node.name:

#                     pkt.hop_History.append(node.name)
#                     #print(f"\t[{pkt.taskID}] PKTID: {pkt.id} ARRIVED to {node.name}\t| HISTORY: {pkt.hop_History}")
#                     #print(f"ABBIAMO CONCLUSO LA DISCOVERY at {env.now}. TASK : {pkt.taskID} pktid: {pkt.id}\tStack: {pkt.node_stack}")
#                     startRouteReply(node, neighbors, pkt)
#                 else:

#                     for n in neighbors:
#                         sendPkt(node, n, pkt)
#                         #print(f"\t[{pkt.taskID}] PKTID: {pkt.id} ({node.name}->{n.name})\t| HISTORY: {pkt.hop_History}")

#             # Nella history inserisco i DISCOVERY
#             node.pkt_history.append(pkt.id)

#         elif pkt.mode == Mode.ROUTE_REPLY:
#             if pkt.source == node.name:
#                 # Se la source di questo pacchetto sono io

#                 #! Salviamo l'informazione che ci è giunta
#                 if pkt.taskID not in node.routes:
#                     node.routes[pkt.taskID] = []

#                 if pkt.hop_History not in node.routes[pkt.taskID]:
#                     node.routes[pkt.taskID].append(pkt.hop_History)

#                 #print(f"[{pkt.taskID}] pktID: {pkt.id} ROURE REPLY COMPLETATA! HISTORY: {pkt.hop_History}")
#                 # Trova il task corrispondente in node.tasks usando pkt.taskID
#                 task = next((t for t in node.tasks if t.id == pkt.taskID), None)
#                 # if task:
#                 #     print(f"Trovato task: {task.id} per pktID: {pkt.id}")
#                 # else:
#                 #     print(f"Nessun task trovato con id {pkt.taskID} in node.tasks")

#                 #print(f"CONTROLLO HISTORY DI [{pkt.taskID}] pktID: {pkt.id} time: {env.now} IMPIEGATO: {env.now - task.routeRequestIst}")

#             else:
#                 # Lo mando al prossimo nodo della rete
#                 lastNodeStack = pkt.node_stack.pop()
#                 nextNode = getNextNode(neighbors, lastNodeStack)

#                 # TODO : Controlla questa parte
#                 if nextNode is not None:
#                     sendPkt(node, nextNode, pkt)

#         node.packets.remove(pkt)

#     # ! Route Request
#     for task in node.tasks:
#         if not task.routeRequestIst:
#             # Task è stato appena eseguito, avvio fase di Discovery
#             routeDiscovery(node, task, neighbors)
#             #print(f"[{task.id}] FROM {node.name} START ROUTE DISCOVERY at {env.now}")
#         else:

#             if node.name == task.source_DSR:
#                 timeout = 1             # 1s

#                 # controllo se è scaduto il timeout per spedirlo
#                 if env.now - task.routeRequestIst > timeout:

#                     # Controllo se mi sono arrivate delle routes
#                     if task.id in node.routes:
#                         #print(f"[TRASMISSION] DEL TASK {task.id}! Ecco le route salvate:{node.routes[task.id]}")

#                         nextHop = None
#                         # Finché ho route candidate
#                         while len(node.routes[task.id]) > 0:

#                             # Prendo la più corta
#                             bestRoute = min(node.routes[task.id], key=len).copy()
#                             route = bestRoute.copy()

#                             # Controllo il prossimo hop
#                             nextHopName = route.pop(0)
#                             nextHop = next((n for n in neighbors if n.name == nextHopName), None)

#                             if nextHop is not None:
#                                 task.selected_route = route
#                                 break
#                             else:
#                                 node.routes[task.id].remove(bestRoute)  # elimino route non valida e continuo

#                         if nextHop is not None:
#                             #print(f"[{task.id}] ROTTA TROVATA: {bestRoute}")
#                             env.process(sendTask(env, task, node, nextHop, 'DSR'))

#                         else:
#                             #print(f"[{task.id}] Nessuna Route Valida!")

#                             # Puliamo il Task
#                             task.routeRequestIst = None
#                             task.RouteReply = False
#                             task.selected_route = []

#                             if task.id in node.pkt_history:
#                                 node.pkt_history.remove(task.id)
#                     else:
#                         # ! TimeOut Error
#                         #print(f"[TimeOut-Error] Per il task {task.id} non sono arrivate ancora le Routes")
#                         timeout += 1

#             elif node.name == task.dest_node:
#                 node.arrived_tasks.append(task)
#                 node.tasks.remove(task)
#                 task.routingEndTime = env.now
#                 print(f"[{task.id}] CONSEGNATO! ")

#             else:
#                 # $ Devo rispedire il task
#                 #print(f"[{task.id}] {node.name} ha ricevuto il Task, lo reindirizza")
#                 #print(f"\t{task.id}] Route presente nel Task : {task.selected_route}")

#                 nextHopName = task.selected_route.pop(0)
#                 nextHop = next((n for n in neighbors if n.name == nextHopName), None)

#                 if nextHop is not None:
#                     env.process(sendTask(env, task, node, nextHop, 'DSR'))
#                 else:
#                     # ! Route-Error
#                     #print(f"!!!!!!!!!!!!!!!! [{node.name}] ROUTE ERROR! Il satellite {node.name} non ha contatti con {nextHopName}")
#                     #print(f"Neighbors: {[n.name for n in neighbors]}\n")
#                     #print(f"HO RESETTATO IL TASK {task.id}:")
#                     #print(f"\t\t ReqIST (prima) : {task.routeRequestIst}\n\t\tROUTE: {task.selected_route}\n\t\t source: {task.source_DSR}")

#                     # Puliamo il Task
#                     task.routeRequestIst = None
#                     task.RouteReply = False
#                     task.selected_route = []
#                     task.source_DSR = node.name
#                     #print("MODIFICHE EFFETTUATE:")
#                     #print(f"\t\t ReqIST (dopo) : {task.routeRequestIst}\n\t\tROUTE: {task.selected_route}\n\t\t source: {task.source_DSR}")

# | Secondi | Millisecondi |
# | ------- | ------------ |
# | 1       | 1000 ms      |
# | 0,5     | 500 ms       |
# | 0,1     | 100 ms       |
# | 0,01    | 10 ms        |
# | 0,001   | 1 ms         |

def periodic_recall_Routing_monitor(env):
    """
    Questa funzione dovrà scorrere costantemente tutti i task dentro
    la lista dei globali, e costantemente spingerli verso la destinazione.
    """
    interval = config["Routing_Interval"]
    while True:

        if DSR:
            for node in globals.edge_servers:               # Forwarding per ogni nodo
                forward_packet_dsr_best(env, node)
            forward_packet_dsr_best(env, globals.observer)  # Forwarding per l'observer
        else:
            for node in globals.edge_servers:
                yield from node.forward_packet(env)    # Eseguiamo il forwarding

        # TODO DIMINUIRE QUESTO VALORE PER rendere il routing più veloce
        yield env.timeout(interval)












