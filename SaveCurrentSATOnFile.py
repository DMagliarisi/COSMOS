"""
Module to fetch and save current TLE data of active satellites.

This module retrieves the latest TLE (Two-Line Element) data for active
satellites from Celestrak and saves it to a local file for use in
satellite network simulations.
"""
import sys
import requests
import os
from enums import tle_data
from utils import colorize


starlink_url = "https://celestrak.org/NORAD/elements/gp.php?GROUP=STARLINK&FORMAT=TLE"
all_active_url = "https://celestrak.org/NORAD/elements/gp.php?GROUP=ACTIVE&FORMAT=TLE"


"""
Cosa fa:
Questo script fa una richiesta al NOMAD per avere tutti i  dati TLE dei satelliti Starlink.
In seguito li salva in un File: ./Data/tle_data.txt
"""

#Funzione che ritorna i satelliti attivi in formato tle 
def get_active_satellites():
    url = starlink_url
    print(colorize("[TLE] Requesting Data...","yellow"))
    response = requests.get(url) # ! Request
    if response.status_code == 200:
        tle_data = response.text.strip().splitlines() # ? TLE
        print(colorize("[TLE] Data received.","green"))
        return tle_data
    else:
        print(f"Errore nella richiesta: {response.status_code}")
        return None


def saveTLEOnFile() -> tle_data:
    _dir, filename ="./data", "tle_data.txt"
    tle_data = get_active_satellites()
    file_path = f"{_dir}/{filename}"
    
    # Check dir
    if not os.path.exists(_dir):
        try:
            os.makedirs(_dir, exist_ok=True)
        except OSError as e:
            sys.exit(colorize(f"ERRORE: {e}","red"))

    if tle_data:
        try:
            with open(file_path, "w") as file:
                for line in tle_data:
                    file.write(line + "\n")
            print(colorize(f"[TLE] Elaborati Correttamente '{file_path}'","green"))
            return tle_data
        except IOError as e:
            sys.exit(colorize(f"Errore nel salvataggio del file: {e}","red"))
    else:
        print("Nessun dato TLE disponibile per il salvataggio.")

