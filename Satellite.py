"""
    Definition of the Satellite class.

    This module defines the `Satellite` class used to represent satellites
    in the satellite network simulation. It includes attributes for the
    satellite's TLE data, distance from the observer, access point status.
"""

from skyfield.api import EarthSatellite

class Satellite:

    def __init__(self, satellite : EarthSatellite, distance, tle, is_acc_point):
        self.satellite = satellite
        self.distance = distance            # distance rispetto all'OBSERVER
        self.tle = tle                      
        self.is_acc_point = is_acc_point

        self.neighbors = {}                 # Dizionario dei suoi vicinini
        
        self.OGMs_History = []          # Lista OGM visionati in passato
        self.OGMs = []                  # Lista OGM da processare
        self.OGMs_NP = []               # Lista OGM ricevuti Not-Processed
        self.ogm_table = {}             # {'originator': [ 'neighbor': 'count']
        self.ogm_sequence = 0           # Contatore OGM emessi
