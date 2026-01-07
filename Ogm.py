"""
Definition of the OGM (Originator Generated Message) class.

This module defines the `Ogm` class used to represent messages exchanged
between nodes in the satellite network simulation. It includes metadata
about the originator, sender, TTL, sequence number, and origin position, 
and provides utilities for cloning the message for forwarding purposes.
"""
class Ogm:
    def __init__(self, originator, sender, origin_position_vect, is_AP, ttl, sequence_number=0):
        self.originator = originator
        self.sender = sender
        self.ttl = ttl
        self.sequence_number = sequence_number
        
        self.origin_position_vect = origin_position_vect    # Posizione attuale del Nodo 
        self.is_AP = is_AP                                  # Booleano che ci informa se il nodo è un AP

        self.id = f"{originator}_{sequence_number}" # identificatore unico

    def clone_for_forwarding(self, new_sender):
        return Ogm(
            originator=self.originator,
            sender=new_sender,
            origin_position_vect = self.origin_position_vect,
            is_AP = self.is_AP,
            ttl=self.ttl - 1,
            sequence_number=self.sequence_number
        )
    
    def __str__(self):
        return f"[{self.id}] SENDER:{self.sender} TTL:{self.ttl}"
