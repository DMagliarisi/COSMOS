"""
Observer management and utility functions for task and position handling.

This module provides:

1. `Observer` class:
   - Represents the simulation observer, tracking tasks, packets, and OGM
     history.
   - Provides methods to retrieve geolocation and projected 3D position
     vectors.
   - Supports printing task summaries in a formatted table.

2. `ObserverMeta` singleton metaclass:
   - Ensures only one Observer instance exists at a time.

3. Utility functions:
   - `format_mb`: formats memory values (MB/GB) for display purposes.
"""
import globals
from collections import OrderedDict
from skyfield.api import wgs84
import sys
import json5

config = globals.config

def format_mb(mb_value):
    """
    Formats the MB value to a clean string.
    :param mb_value: Value in Megabytes (MB).
    :return: Formatted string (e.g., "5.25 MB").
    """
    if mb_value >= 1024:
        # Se è troppo grande, converti in GB per pulizia, altrimenti mantieni MB.
        gb_value = mb_value / 1024
        return f"{gb_value:.2f} GB"
    return f"{mb_value:.2f} MB"

class ObserverMeta(type):
    """
    Singleton instance of an Observer
    """
    _instances = {}

    def __call__(cls, *args, **kwargs):

        if cls not in cls._instances:
            instance = super().__call__(*args, **kwargs)
            cls._instances[cls] = instance
        return cls._instances[cls]
    

class Observer(metaclass = ObserverMeta):
    def __init__(self, env, pos):
        
        self.env = env
        self.position = pos     # Posizione dell'observers

        self.name = 'OBS' 
        self.is_acc_point = False
        
        self.tasks = []
        self.arrived_tasks = []
        self.OGMs_position = {}
        self.ogm_sequence = 0               # Contatore OGM emessi
        self.OGMs = []                      # OGM to process
        self.OGMs_NP = []                   # OGM recived and Not-Processed
        self.ogm_table = {}                 # OGMs Table {'originator': [ 'neighbor': 'count']
        self.OGMs_History = OrderedDict()   # Lista OGM visionati in passato

        self.packets = []           # Lista di pacchetti da smaltire
        self.packets_seq = 0        # contatore pacchetti spediti
        self.pkt_history = []

    
    def getLocation(self, altitude = config["sphere_altitude_km"] ,location = config["simulation_location"]):
        if location in config["locations"]:
            lat = config["locations"][location]["lat"]
            lon = config["locations"][location]["lon"]
            #print(f"User Location: {location} ({lat},{lon}) With Altitude ")

            return wgs84.latlon(lat, lon, altitude)
        else:
                sys.exit(f"Errore: The User Position '{location}' not found in the config file.") 
    
    def getPositionVector(self, t):
        """
        Questa funzione ritorna un vettore in 3 dimensioni,
        della posizione dell'observer proiettato nello spazio.
        """

        return self.getLocation(altitude = 0).at(t).position.km.tolist()
    

    def print_task_summary(self):
        """
        Stampa un riassunto formattato dei task arrivati con successo all'Observer
        """
        print(f"TASK ARRIVATI CON SUCCESSO ALL'OBS: {len(self.tasks)}:\n")
        print(" id     | weight        | resolution    | Start Routing (s) | End Routing (s) | duration      | hop |")
        for task in self.tasks:

            initTime = round(task.routingInitTime, 2)
            if task.routingEndTime is None:
                endTime = "N/A"
                duration = "N/A"
            else:
                endTime = round(task.routingEndTime, 2)
                duration = round(task.routingEndTime - task.routingInitTime, 2)

            print(
                f" {task.id:<6} | {format_mb(task.weight):<13} | {task.task_type:<23} | {initTime:<17} | {endTime:<15} | {duration:<13} | {task.hop:<3} |")
        print()  # Riga vuota alla fine per separare dall'output successivo
