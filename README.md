# Handling Heterogeneous Satellite Computing Services with Energy and QoS Constraints

This repository contains the Discrete Event Simulator (DES) and the source code used for the experimental evaluation.

The project simulates a Low Earth Orbit (LEO) satellite mega-constellation (based on Starlink orbital parameters) to evaluate various Satellite Computing Service Handling (SCSH) strategies and Routing protocols under heterogeneous workloads and strict constraints (energy, deadline, storage).

## 🚀 Key Features

Realistic LEO Constellation: Utilizes SGP4 propagator with real TLE data (Starlink).

Heterogeneous Environment: Models satellites (SENs) with varying CPU capacities, memory, storage, and energy budgets.

Service Handling Strategies:

    Heuristics: DTS-base, DTS-APopt, OrbitAware.

    Optimization: ILP (Integer Linear Programming) and ILPH (Hierarchical ILP) using Weighted and Hierarchical approaches.

Routing Protocols:

        Reactive: Greedy (Geographic) and Dynamic.

        Proactive: B.A.T.M.A.N. (Better Approach to Mobile Ad-hoc Networking).

Metrics: Success Rate, Response Time, Energy Consumption, Load Balancing Degree, Hop Count, and Routing Overhead.
