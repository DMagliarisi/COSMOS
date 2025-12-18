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