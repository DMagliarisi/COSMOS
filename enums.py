"""
Type definitions and enumerations for configuration management.

This module defines shared type aliases, enumerations, and TypedDict
structures used to formalize data types involved in configuration
management.
"""
from enum import StrEnum, auto
from typing import TypedDict, Any, List, Dict

tle_data = list[str]    # Type alias for TLE data representation

class AccPointMode(StrEnum):
    OPTIMAL = auto()    # "STATIC"
    BASE    = auto()    # "DYNAMIC"

# Alias per il tipo di ritorno di genConfigs() in topology.py
class GenConfigsConfigEntry(TypedDict):
    time: str
    configuration: List[Dict[str, Any]]

class GenConfigsOutput(TypedDict):
    t0: str
    interval: int
    total_seconds: int
    observer_position: Any
    configurations: List[GenConfigsConfigEntry]
