import json5
from math import sqrt
from datetime import timedelta
from skyfield.api import load, EarthSatellite, wgs84
from skyfield.timelib import Time
from Satellite import Satellite
import sys
from enums import AccPointMode
import globals
from utils import colorize

# Leggi il file di configurazione JSON
config = globals.config

ts = load.timescale()                                   # ts : time management with astronomical time
time_now = ts.now()

# reading TLE DATA from File
def loadTLEFromFile(filename):
    try:
        with open(filename, "r") as file:
            tle_data = file.read().splitlines()
        return tle_data
    except FileNotFoundError:
        print(colorize(f"[TLE] Il file {filename} non è stato trovato.","red"))
        return None
    except IOError as e:
        print(colorize(f"[TLE] Errore nella lettura del file: {e}","red"))
        return None

TLE_DATA = loadTLEFromFile("./data/tle_data.txt")       # Load TLE Data 
# ---------------------------------------------------------------------------- #
#                                    Printer                                   #
# ---------------------------------------------------------------------------- #

def printSatList(*sats):
    for l in sats:
        print("-"*20)
        [print(f"SATELLITE: {s[0].name} Distance: {s[1]}") for s in l]


# ---------------------------------------------------------------------------- #
#                                    Getter                                    #
# ---------------------------------------------------------------------------- #

def get_current_time() -> Time:
    """
    Get the current time using Skyfield's timescale.

    This function loads the timescale from the Skyfield library and returns the current time.

    Returns:
        skyfield.timelib.Time: The current time according to Skyfield's timescale.
    """
    ts = load.timescale()  # Carica la scala temporale di Skyfield
    return ts.now()   

def advance_time(current_time, minutes_to_add):
    """
    Advance the given Skyfield Time object by a specified number of minutes.

    Args:
        current_time (skyfield.timelib.Time): The current time from ts.now().
        minutes_to_add (int): The number of minutes to add to the current time.

    Returns:
        skyfield.timelib.Time: The new time advanced by the specified number of minutes.
    """
    # Convert the Skyfield Time object to a datetime object
    current_datetime = current_time.utc_datetime()
    
    # Add the specified number of minutes
    new_datetime = current_datetime + timedelta(minutes=minutes_to_add)
    
    # Convert the new datetime object back to a Skyfield Time object
    ts = load.timescale()
    new_time = ts.from_datetime(new_datetime)
    
    return new_time

# Getter Topos 'Observer' object 
def getObserverObj(location = config["simulation_location"]):
    if location in config["locations"]:
        lat = config["locations"][location]["lat"]
        lon = config["locations"][location]["lon"]
        return wgs84.latlon( lat, lon)
    else:
        sys.exit(f"Errore: The User Position '{location}' not found in the config file.") 

OBSERVER = getObserverObj()

# Getter reference system from a Satellite 
def getSystemFromSat(satellite, time ,Geocentric = False ):
    """
    Get the reference system from a satellite.

    Args:
        satellite (EarthSatellite): The satellite object.
        Geocentric (bool): If False, return the system relative to the observer's point of view.
                           If True, return the satellite's position relative to the center of the Earth (Geocentric).
        time (Time): The time at which to get the satellite's position.
    Returns:
        Geocentric or Topocentric: The reference system of the satellite at the given time.
    """
    if Geocentric : # Satellite Position from the Center of Earth
        return satellite.at(time)                   # return geocentric system  
    else:           # Satellite Position from the Observer Position
        difference = satellite - OBSERVER   # Calculate the difference between the satellite's position and the observer's position to get the topocentric reference system
        return difference.at(time)                  # return topocentric system
    
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

def get_orbit_proximity(sat1 , sat2, t):
    """
    Calculates the distance between two satellites in kilometers.

    Args:
        satellite1: EarthSatellite object representing the first satellite.
        satellite2: EarthSatellite object representing the second satellite.
        timestamp: Skyfield Time object representing the moment of calculation.

    Returns: The distance between the two satellites in kilometers.
    """
    # Get geocentric positions in km
    pos_sat1 = getSystemFromSat(sat1, t, True).position.km
    pos_sat2 = getSystemFromSat(sat2, t, True).position.km

    # Extraction of coordinate components
    x1, y1, z1 = pos_sat1
    x2, y2, z2 = pos_sat2

    return sqrt((x2 - x1)**2 + (y2 - y1)**2 + (z2 - z1)**2)     # Calculate the Euclidean distance

def are_satellites_equal(sat1, sat2):
    """
    Compare two satellites to check if they are the same based on their name and satellite number.

    Args:
        sat1: EarthSatellite object representing the first satellite.
        sat2: EarthSatellite object representing the second satellite.

    Returns:
        bool: True if the satellites are the same, False otherwise.
    """
    # Confronta per nome e numero satnum
    return (
        sat1.name == sat2.name and
        sat1.model.satnum == sat2.model.satnum
    )

def getLatency(distance:float):
    LIGHT_SPEED = 299792458  #m/s
    # convert distance to meters
    distance_m = distance * 1000

    return distance_m / LIGHT_SPEED  # Calculate latency in seconds

# ---------------------------------------------------------------------------- #
#                                    Filter                                    #
# ---------------------------------------------------------------------------- #

def filterSatellitesInView(satellite, t):
    """
    Input: Satellites
    Output: Boolean value | True : sat is coming in our direction
                          | False : sat is not coming in our direction
    """
    sys = getSystemFromSat(satellite, t) 
    
    velocity = sys.velocity    

    r = sys.position.km                             # r è la posizione relativa del SAT rispetto all'observer
    r_unit = r / globals.np.linalg.norm(r)                  # Vettore unitario
    v_rel = globals.np.dot(velocity.km_per_s, r_unit)       # Calcoliamo la velocità calcolando il prodotto scalare tra r e r_unit
    
    return True if v_rel < 0 else False

# ---------------------------------------------------------------------------- #
def getAllSatOnMe(t, tle_data, ap_mode : AccPointMode, Phi_max = config["Phi_max"], Num_Access_point = config["access_point"]):
    
    buffer_Phi = Phi_max - config["Phi_buffer"]             # Angle of a Buffer Zone
    satellites_dome, satellites_buffer = [], []             

    # Prendo tutti i satelliti nella mia Cupola e BufferZone
    for i in range(0, len(tle_data), 3):
        name = tle_data[i].strip()
        line1 = tle_data[i + 1].strip()
        line2 = tle_data[i + 2].strip()

        satellite = EarthSatellite(line1, line2, name, ts)  # Converting tle Data in SGP4 Satellite Object
        syst = getSystemFromSat(satellite, t)        # reference system 
        
        alt, az, distance = syst.altaz()                     # alt : Altitude in degrees relative to the observer
                                                            # az : Sat Azimuth Angle relative to the observer
                                                            # distance: distance Sat - Observer
        if alt.degrees > buffer_Phi:
            if alt.degrees > Phi_max:
                # Satellite in the Dome
                satellites_dome.append(Satellite(satellite, distance.km, (name, line1, line2), False))
            else:
                # Satellite in the Buffer Zone
                satellites_buffer.append(Satellite(satellite, distance.km, (name, line1, line2), False))


    #Ordino i satelliti in base alla posizione rispetto all'utente
    sat_sort_dome = sorted(satellites_dome, key=lambda s: s.distance)
    sat_sort_buff = sorted(satellites_buffer, key=lambda s: s.distance)
    
    counter, acc_points, dome = 0, [], []
    if Num_Access_point > len(sat_sort_dome):
        Num_Access_point = len(sat_sort_dome) // 2   # Non ci sono abbastanza satelliti da soddisfare la richiesta di Access_point 
        print(f"WARNING: Not enough satellites to satisfy the request. The number of access points has been set to {Num_Access_point}.")

    # Determino Access Points
    for sat in sat_sort_dome:

        # Decido se eseguire il filtro o meno
        if ap_mode == AccPointMode.BASE:
            can_take = (counter < Num_Access_point)
        elif ap_mode == AccPointMode.OPTIMAL:
            can_take = (counter < Num_Access_point and filterSatellitesInView(sat.satellite, t))

        if can_take:
            sat.is_acc_point = True
            dome.append(sat)
            counter+=1
        else:
            dome.append(sat)

    return dome, sat_sort_buff

def compute_distances_from_target_satellite(sat, closerSatellite_Sorted, t):
    """
    Compute the distances from the target satellite to other satellites.

    :param sat: The target satellite.
    :param closerSatellite_Sorted: List of satellites sorted by proximity.
    :param t: Current time.

    :return: List of tuples containing satellites and their distances from the target satellite.
    """
    
    vector_Sat_Topology = []
    for i in range(0, len(closerSatellite_Sorted)):
        if are_satellites_equal(sat.satellite, closerSatellite_Sorted[i].satellite):
            pass
        else:
            proximity = get_orbit_proximity(sat.satellite , closerSatellite_Sorted[i].satellite, t)
            if  proximity < config["Laser_Communication_Range"] :                                # Check laser distance
                latency = getLatency(proximity)                                                  # Calculate latency
                vector_Sat_Topology.append((closerSatellite_Sorted[i].satellite.name, proximity, latency))

    sat_vector_Topology_sorted = sorted(vector_Sat_Topology, key=lambda x: x[1])                # Ordino i vicini in base alla distanza dal satellite
    return sat_vector_Topology_sorted


def find_angle(t, satellite):
    """
    Find the elevation angle of a satellite relative to the observer at a given time.

    Args:
        t (skyfield.timelib.Time): The time at which to find the elevation angle.
        satellite (EarthSatellite): The satellite object.

    Returns:
        float: The elevation angle in degrees.
    """
    sys = getSystemFromSat(satellite, t)
    alt, az, distance = sys.altaz()
    return alt.degrees
    

def create_satellite_Identity_card(satellite, life, neighbors_info, t, access_points = False):
    """
    Create a dictionary representing a satellite and its neighbors.

    Args:
        satellite (tuple): A tuple containing the satellite object and its distance from the user.
        life (dict): A dictionary with information about the satellite's life.
        neighbors_info (list of tuples): A list of tuples with (neighbor_name, distance, latency).
        t (skyfield.timelib.Time): The current time.
        access_points (bool): Whether the satellite is an access point.

    Returns:
        dict: A dictionary representing the satellite and its neighbors.
    """
    elevation_angle = find_angle(t, satellite.satellite)  # Mi trovo l'angolo di elevazione del satellite rispetto l'obverver

    satellite_data = {
        "satellite": satellite.satellite.name,
        "distance_from_user": satellite.distance,
        "is_access_point": access_points,
        "elev_angle": elevation_angle,
        "TLE-DATA": [{"name": satellite.tle[0], "line1": satellite.tle[1], "line2": satellite.tle[2]}],
        "life": life,
        "neighbors": [
            {"name": neighbor_name, "distance": distance, "latency": latency}
            for neighbor_name, distance, latency in neighbors_info
        ]
    }
    return satellite_data

def classifySat_BufferZone(buffer_satellites, time = time_now):
    time_end = ts.utc(time.utc_datetime() + timedelta(minutes=40))
    selected_satellites = []

    min_elev_cone = 90 - config["Phi_max"]   # Quantità di gradi al di sotto della soglia dove osservo.
    buffer_Phi = config["Phi_max"] - config["Phi_buffer"]             # Angle of a Buffer Zone

    for s in buffer_satellites:
        # Calcolo eventi di passaggio
        t, events = s[0].find_events(OBSERVER, time, time_end, altitude_degrees=buffer_Phi)
        max_elevation = 0                   # ? Masimo punto di elevazione del satellite

        for ti, event in zip(t, events):
            if event == 1:  # Max Alt

                syst = getSystemFromSat(s[0], time = time)      # reference system 
                alt, az, distance = syst.altaz() 

                max_elevation = alt.degrees  # Elevazione in gradi
                break
        print(f"SAT: {s[0].name} - Elevation: {max_elevation} >= {min_elev_cone}") 
        # Verifica se il satellite raggiunge almeno la soglia del cono di osservazione
        if max_elevation >= min_elev_cone :
            selected_satellites.append((s[0], s[1], max_elevation))
    
    return selected_satellites

# ---------------------------------------------------------------------------- #

def check_satellite_visibility(satellite, location, start, end):
    # Intervallo di tempo con step di 1 minuto
    times = ts.utc_range(start.utc_datetime(), end.utc_datetime(), timedelta(minutes=1))
    
    # Trova elevazioni per ogni punto temporale
    elevations = []
    for t in times:
        difference = satellite - location
        topocentric = difference.at(t)
        alt, _, _ = topocentric.altaz()
        elevations.append((t, alt.degrees))
    
    # Filtra le elevazioni tra 20° e 40° e verifica se superano i 40°
    between_20_40 = any(20 <= alt <= 40 for _, alt in elevations)
    exceeds_40 = any(alt > 40 for _, alt in elevations)
    
    return between_20_40 and exceeds_40


    