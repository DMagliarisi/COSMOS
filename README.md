
# **Satellite Computing Discrete Event Simulator**

## 📖 Overview

This repository contains the **Discrete Event Simulator (DES)**, which enables the simulation of a satellite computing system deployed on a **Low Earth Orbit (LEO)** satellite mega-constellation, based on **Starlink** orbital parameters.

The simulator has been used to evaluate **Satellite Computing Service Handling (SCSH)** strategies and satellite routing protocols under heterogeneous workloads and strict constraints.

## 🚀 Key Features

* **Realistic LEO Constellation:** Utilizes SGP4 propagator with real TLE data (Starlink) to model orbital mechanics and visibility windows.
* **Heterogeneous Environment:** Models Satellite Edge Nodes (SENs) with varying CPU capacities, memory limits, storage, and energy budgets.
* **Heterogeneous Workload:** Supports the generation and handling of diverse service types:
- *Generic services* (Low resource demand).
- *CPU-intensive services* (High computational cost).
- *CPU and Data-intensive services* (High computation and transmission requirements).
- *Batch Data-intensive services* (Large file transfers).


* **Service Handling Strategies:** Implements and compares heuristic strategies (`DTS-base`, `DTS-APopt`, `OrbitAware`) against optimization-based approaches (`ILP`, `ILP-Hierarchical`).
* **Routing Protocols:** Includes implementations of Proactive (**B.A.T.M.A.N.**) and Reactive (**Greedy**, **Dynamic**) routing algorithms for ISL (Inter-Satellite Link) communication.

---

## 🏃 Usage & Execution

The simulator is configured via the `config.json5` file. The execution flow requires two steps: first, generating the constellation topology/orbits, and second, running the actual simulation experiments.

### 1. Configuration Setup

Ensure the configuration files are present in the main directory:

* `config.json5`: Main simulation parameters.
* `img_resolution.json5`: Task size and resolution definitions.

### 2. Step 1: Topology Generation

Before running any workload simulation, the satellite orbital positions and contact plans must be generated.

1. Open `config.json5`.
2. Set the following parameters:

```json5
{
  "Build_Configurations": true,
  "Load_Configuration": false,
  // ... other parameters
}

```

3. Run the script:

```
python main.py

```

> This process generates the orbital data and saves it locally. It will exit automatically upon completion (*"File of configurations created"*).

### 3. Step 2: Running the Simulation

Once the topology is built, you can run the experiments.

1. Open `config.json5`.
2. Switch the mode to loading:

```json5
{
  "Build_Configurations": false,
  "Load_Configuration": true,
  // ...
}

```

3. Run the simulation:

```
python main.py

```

### 4. Reproducing Specific Strategies

To reproduce the specific strategies discussed in the paper (**DTS-base**, **DTS-APopt**, **OrbitAware**, **ILP**), you must modify the specific parameters in `config.json5` as per the table below:

| Strategy | `request_distribution` | `AP_selection` | `SearchNode` |
| --- | --- | --- | --- |
| **DTS-base** | `"DTS-base"` | `"base"` | `"ERT"` |
| **DTS-APopt** | `"DTS-base"` | `"optimal"` | `"ERT"` |
| **OrbitAware** | `"OrbitAware"` | `"optimal"` | `"ERT"` |
| **ILP** | `"DTS-base"` | `"optimal"` | `"ILP"` |

**Example configuration for OrbitAware:**

```json5
{
  "request_distribution": { "distribution": "OrbitAware" },
  "AP_selection": "optimal",
  "SearchNode": "ERT",
  "DTS": true,
  // ...
}

```

### 5. Reproducing Sensitivity Analyses

To reproduce the graphs (e.g., varying arrival rates or energy budgets), you need to manually update the corresponding value in `config.json5` and re-run `main.py` for each data point.

* **Varying Workload:** Change `arrival_time_exponential` (lower value = higher frequency).
* *Note:* The paper refers to req/s (). If the config expects inter-arrival time (seconds), .
* *Example for 10 req/s:* `"arrival_time_exponential": 0.1`


* **Varying Energy:** Change `"initial_energy"` (e.g., `80000`, `100000`, `120000`).

### 6. Output Data

The simulation results are saved in the `result/` directory, structured by experiment parameters (Strategy, Deadline, Energy, Seed).

* **Results CSV:** Contains detailed metrics for every task (Completed/Rejected).
* **Migration CSV:** Tracks task relocations.
* **Energy Stats:** Aggregated energy consumption per satellite.
