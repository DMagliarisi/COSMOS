
from enum import Enum

class Mode(Enum):
    ROUTE_DISCOVERY = "ROUTE DISCOVERY"
    ROUTE_REPLY = "ROUTE REPLY"
    # ROUTE_ERROR = 3
    # DATA = 4
    
    def __str__(self):
        return self.name


class Packet:

    def __init__(self, id: str, taskID:int, source: str, dest: str, mode: Mode):
        
        self.id = id
        self.taskID = taskID
        self.source = source
        
        self.dest = dest
        self.mode = mode

        self.current_node = source

        self.visited = set()        # Set di Nodi Visitati
        self.visited.add(source)

        self.hop_History = []       # route Tracker
        self.node_stack = [source]  # back walk Tracker

    def duplicate(self):
        new_pkt = Packet(
            self.id,
            self.taskID,
            self.source,
            self.dest,
            self.mode
        )
        new_pkt.current_node = self.current_node
        new_pkt.hop_History = list(self.hop_History)
        new_pkt.visited = set(self.visited)
        new_pkt.node_stack = list(self.node_stack)
        return new_pkt
    
    def __str__(self):
        return (f"Packet(id={self.id}, taskID={self.taskID}, source={self.source}, "
                f"mode={self.mode}, current_node={self.current_node}, "
                f"hop_History={self.hop_History})")