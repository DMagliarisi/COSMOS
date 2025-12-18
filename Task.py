import sys
import globals
import json5, csv
import os
from pprint import pprint

from utils import colorize

config = globals.config
resolution = globals.resolution_config


BATMAN = config["Routing_algorithm"]["BATMAN"]
GREEDY = config["Routing_algorithm"]["GREEDY"]
DSR = config["Routing_algorithm"]["DSR"]


class Task:

    def __init__(self, task_id: int, current_node: str, dest_node: str, routingInitTime, task_type, image_size):

        self.id = task_id
        self.ttl = 20  # Time to live in HOP
        self.hop = 0  # num_hop
        self.arrived = False  # Arrived Flag
        self.routingInitTime = routingInitTime  # Tempo di partenza
        self.routingEndTime = None  # Tempo di fine
        self.label = 'ON_SIMULATION'  # Failure Label

        self.task_type = task_type  # Es: "CPU_Intensive"
        self.weight = image_size  # Dimensione Immagine (MB)

        self.source = current_node  # Nodo di partenza

        self.current_node = current_node  # Server sul quale si trova
        self.dest_node = dest_node  # Nodo di destinazione

        # self.visited: set[str] = {current_node}# Set Server precedente
        self.visited = set()  # Set Server precedente
        self.visited.add(current_node)  # Aggiungo il primo server (il nome!)

        self.algorithms_used = {}  # Dizionario degli algoritmi utilizzati

        self.hop_History = [current_node]  # Lista di satelliti sui quali sono stato

        # DSR
        self.source_DSR = current_node
        self.routeRequestIst = None  # Timestamp start route Request
        self.RouteReply = False  # Bool allow reply
        self.selected_route = []  # Lista percorso da seguire
    
    def __str__(self):
        """
        String representation of the Task object.
        """
        if self.routingEndTime:
            duration = self.routingEndTime - self.routingInitTime
            return f"|ID:{self.id}\t|Hop:{self.hop}|Init:{self.routingInitTime}|Endt:{self.routingEndTime}|dur:{duration}"

    def add_algorithm(self, algo_name: str):
        """Incrementa il contatore per l'algoritmo usato"""
        if algo_name not in self.algorithms_used:
            self.algorithms_used[algo_name] = 0
        self.algorithms_used[algo_name] += 1

    def get_stat_csv(self) -> tuple:
        if self.routingEndTime:
            duration = self.routingEndTime - self.routingInitTime 
            return self.hop, self.routingInitTime, self.routingEndTime, duration
        else:
            return self.hop, self.routingInitTime, "N/A", "N/A"


def _win_longpath(p: str) -> str:
    """Rende il path compatibile con i percorsi lunghi di Windows usando il prefisso \\?\\."""
    if os.name != "nt":
        return p
    # normalizza/assolutizza
    p = os.path.abspath(p)
    if p.startswith("\\\\?\\"):
        return p  # già esteso
    if p.startswith("\\\\"):        # UNC -> \\?\UNC\server\share\...
        return "\\\\?\\UNC" + p[1:]
    return "\\\\?\\" + p



def assign_resolution(required_ram, required_disk):
    # Normalizzazione pesata
    norm_ram = required_ram / config["required_ram"]["max"]
    norm_disk = required_disk / config["required_disk"]["max"]
    weight = 0.5 * norm_ram + 0.5 * norm_disk

    # Mappatura del peso a una categoria
    if weight <= 0.25:
        category = "Low"
    elif weight <= 0.5:
        category = "Medium"
    elif weight <= 0.75:
        category = "High"
    else:
        category = "Very High"

    min_value = resolution[category]["min"]
    max_value = resolution[category]["max"]

    min_byte = dim_to_Byte(min_value["dim"], min_value["value"])
    max_byte = dim_to_Byte(max_value["dim"], max_value["value"])

    resolution_value = globals.rnd.randint(min_byte, max_byte)  # Valore di Ritorno in Byte
    return category, resolution_value


def dim_to_Byte(dim, value):
    """
    Converts megabytes (MB) or kilobytes (KB) to bytes.
    :param dim: Dimension unit ('MB' or 'KB').
    :param value: Value in the given unit.
    :return: Value in bytes.
    """
    if dim == 'MB':
        return int(value * 1000 * 1000)
    if dim == 'KB':
        return int(value * 1000)


def byte_to_dim(byte_value):
    """
    Converts bytes to the most suitable unit (MB, KB, or B) and returns a formatted string.
    :param byte_value: Value in bytes.
    :return: String in the format "value unit" (e.g., "2.5 MB").
    """
    if byte_value >= 1000 * 1000:
        value = byte_value / (1000 * 1000)
        unit = 'MB'
    elif byte_value >= 1000:
        value = byte_value / 1000
        unit = 'KB'
    else:
        value = byte_value
        unit = 'B'
    return f"{value:.2f} {unit}"


def get_algo_percentages(t):
    """
    Calcola le percentuali di utilizzo degli algoritmi per un Task.
    Ritorna un dizionario {algoritmo: percentuale}.
    """
    algo_perc = {}
    total = sum(t.algorithms_used.values())
    if total > 0:
        for algo, count in t.algorithms_used.items():
            algo_perc[algo] = round((count / total) * 100, 2)
    return algo_perc  # se non ci sono algoritmi rimane {}


def findAlgorithm():

    if BATMAN and GREEDY and not DSR:
        return "DINAMICO"
    elif BATMAN and not GREEDY and not DSR:
        return "BATMAN"
    elif GREEDY and not BATMAN and not DSR:
        return "GREEDY"
    elif DSR and not BATMAN and not GREEDY:
        return "DSR"
    else:
        sys.exit(
            f"C'è un problema con gli algoritmi: DSR:{DSR} GREEDY:{GREEDY} BATMAN:{BATMAN}")


def makeSummary(folder, TArr, TExp, Tsob, ToS):
    file = f"SUMMARY[{findAlgorithm()}]_Rout_interval_{str(config.get('Routing_Interval')).replace(',', '.')}.csv"

    # 1) Assicurati che la cartella esista (usando il path assoluto)
    folder_abs = os.path.abspath(folder)
    os.makedirs(folder_abs, exist_ok=True)

    # 2) Costruisci path e applica il prefisso long-path su Windows
    path = os.path.join(folder_abs, file)
    path = _win_longpath(path)

    summary_row = [config["seed"], len(globals.gbl_tasks), TArr, TExp, Tsob, ToS]

    file_exists = os.path.isfile(path)
    with open(path, mode="a", newline="", encoding="utf-8") as summary_file:
        writer = csv.writer(summary_file)
        if not file_exists:
            writer.writerow(["Seed", "TotalTasks", "Arrived", "Expired", "OutOfBuff", "OnSim"])
        writer.writerow(summary_row)

    print(f"Summary info saved to: {path}")



def convert_task_list_in_dict(task_list : list) -> dict:
    result = {}
    for elem in task_list:
        # supporta sia oggetti con attributo .id che dict con chiave 'id'
        if isinstance(elem, dict):
            key = elem.get("id")
        else:
            key = getattr(elem, "id", None)

        if key is None:
            continue

        result[key] = elem
        # stampa lo stato corrente del dizionario dopo ogni inserimento
    return result

def get_routing_hop_tasks(task_id: int) -> int:
    pass


def generate_Tasks_Status(csv_filename, folder):
    """
        Questa funzione salva in un file CSV le informazioni sui Task
    """

    total_tasks = []
    print(f"TASK GLOBALI {len(globals.gbl_tasks)} \n", )

    for t in globals.gbl_tasks:
        # Se il task ha un tempo di fine routing, calcola la durata e arrotonda i valori
        if t.routingEndTime is not None:
            durata = round(t.routingEndTime - t.routingInitTime, 2)
            routing_end = round(t.routingEndTime, 2)
        else:
            durata = None
            routing_end = None

        # Crea una lista con le informazioni principali del task
        elem = [
            t.id,  # ID del task
            t.current_node,  # Nodo corrente
            t.hop,  # Numero di hop
            t.label,  # Etichetta di stato
            t.task_type,  # Categoria di task_type
            round(t.routingInitTime, 2),  # Tempo di inizio routing arrotondato
            routing_end,  # Tempo di fine routing arrotondato (se presente)
            durata,  # Durata del routing (se presente)
            get_algo_percentages(t)
        ]
        # Aggiungi le informazioni del task alla lista totale
        total_tasks.append(elem)

    # FASE DI SORTING
    total_tasks.sort(key=lambda x: x[0])

    TArr, TExp, Tsob, ToS = 0, 0, 0, 0

    # FASE DI STAMPA FORMATTATA
    print(f"TOT TASK IN ROUTING SYS: {len(total_tasks)}\n")
    print(
        "id     | CurrentNode          | Hop | Label           | Resolution    | Start Routing (s) | End Routing (s) | duration      | Algo")
    for elem in total_tasks:
        id_, current_node, hop, label, task_type, routing_start, routing_end, durata, algorithms = elem
        algorithms_str = ', '.join([f"{k}:{v}%" for k, v in algorithms.items()]) if algorithms else "-"

        row = (
            f"{id_:<6} | {str(current_node):<20} | {hop:<3} | {label:<15} | "
            f"{task_type:<22}| {routing_start:<17} | {str(routing_end):<15} | "
            f"{str(durata):<13} | {algorithms_str}"
        )

        if label == "TASK_ARRIVED":
            row = colorize(row, "green")
            TArr += 1
        elif label == "TTL_EXPIRED":
            row = colorize(row, "red")
            TExp += 1
        elif label == "SEN_OUT_OF_BUFF":
            row = colorize(row, "orange")
            Tsob += 1
        else:
            ToS += 1

        print(row)

    makeSummary(folder, TArr, TExp, Tsob, ToS)  # Genera il Summary

    print()  # Riga vuota alla fine per separare dall'output successivo

    # FASE DI SCRITTURA CSV
        # FASE DI SCRITTURA CSV
    headers = [
        "TaskID", "CurrentNode", "Hop", "Label", "Resolution",
        "RoutingInitTime", "RoutingEndTime", "Duration", "Algorithms"
    ]

    # Assicura l'esistenza della cartella del CSV (uso path assoluto)
    csv_dir = os.path.abspath(os.path.dirname(csv_filename) or ".")
    os.makedirs(csv_dir, exist_ok=True)

    csv_path = os.path.join(csv_dir, os.path.basename(csv_filename))
    csv_path = _win_longpath(csv_path)  # <--- prefisso long-path su Windows

    with open(csv_path, mode="w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(headers)
        writer.writerows(total_tasks)

    print(f"Task info saved to: {csv_path}")