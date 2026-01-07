"""
Module for simulating the OGM (Originator Generated Message) environment.

This module sets up a simulation environment to process OGMs exchanged
between satellites and an observer. It includes functions to run the
simulation for a specified duration, allowing for the pre-loading of OGM
tables, and to clean up initial configurations used for table filling.
"""
import sys
import simpy
from enums import AccPointMode
import globals
import json, json5
from  Observer import Observer 
from user_based_topology import getObserverObj
from topology import distribute_ogm, loadConfiguration, loadConfiguration_simple
from routing_Manager import periodic_recall_Routing_monitor

import time

config = globals.config

ADDING_TIME = config["adding_time"] # secondi di aggiunta al tempo di simulazione per il riempimento delle tabelle OGM
CONFIG_RIEMPI = ADDING_TIME // 2    # configurazioni aggiuntive da rimuovere



def process_OGM_enviroment_simulation(data_configuration) -> None:
    """
    Funzione usata per la costruzione delle tabelle OGM nella fase di PRE-Loading
    """
    
    globals.rnd.seed(config["seed"])

    env_ogm = simpy.Environment()  
    simulation_duration = config['simulation_duration'] + ADDING_TIME    #Aggiungo 60 secondi (30 conf) di simulazione per il caricamento delle tabelle    
    print(f"--SIMULAZIONE OGM DURATA TOTALE: {simulation_duration} secondi")

    globals.observer = Observer(env_ogm, getObserverObj())  # Singleton Observer
    env_ogm.process(distribute_ogm(env_ogm, data_configuration, CONFIG_RIEMPI))    # ! Processo di redistribuzione

    env_ogm.run(simulation_duration)
    
    


def remove_first_30_configurations(mode:AccPointMode) -> None:
    """
    Effettua la rimozione delle prime 30 configurazioni (60 secondi) usate per il riempimento delle tabelle OGM.
    Durante il processo di creazione delle configurazioni, vengono aggiunte 30 configurazioni extra per permettere il
    corretto riempimento delle tabelle OGM.
    """
    config_path = f"data/configurations_AP_{mode}.json"
    with open(config_path, "r") as f:
        data = json.load(f)

    config_list = data["configurations"]
        
    print(f"inizialmente: {len(config_list)}")
    del config_list[:CONFIG_RIEMPI]    # Elimino le conf di riempimento delle table
    print(f"Elimino i primi di riempimento: {len(config_list)}")
    
    data["configurations"] = config_list
    data["t0"] = config_list[0]["time"]
    data["total_seconds"] = config['simulation_duration']
    
    print(f"total_second: {data['total_seconds']} <= {config['simulation_duration']}")
    print(f"tempo {data['t0']} configList tempo {config_list[0]['time']}")
    
    with open(config_path, "w") as f:
        json.dump(data, f, indent=4)
