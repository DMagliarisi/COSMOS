import os
import pandas as pd
import re


def extract_parameters_from_path(root_path, filename, base_dir):
    """
    Estrae i parametri e definisce i nomi univoci per le varianti:
    - DTS-base, DTS-APopt, OrbitAware (dal nome cartella root)
    - ILP Hierarchical (Energy) / (Time)
    - ILP (we=X, wR=Y) per i weighted
    """

    params = {
        'seed': None,
        'routing': None,
        'img_res': None,
        'sim_type': None,
        'solver': None,
        'AT': None,
        'CPU': None,
        'deadline': None,
        'energy_budget': None,
        'w_e': None,
        'w_R': None,
        'ilp_variant': None,  # 'Hierarchical' o 'Weighted'
        'opt_goal': None  # 'Energy' o 'Time'
    }

    try:
        # --- 1. ESTRAZIONE BASE DA FILENAME ---
        if filename.startswith('['):
            solver_match = re.search(r'\[(.*?)\]', filename)
            if solver_match:
                params['solver'] = solver_match.group(1)

        at_cpu_match = re.search(r'AT_([\d\.]+)_CPU_([\d\.]+)\.csv', filename)
        if at_cpu_match:
            params['AT'] = float(at_cpu_match.group(1))
            params['CPU'] = float(at_cpu_match.group(2))

        # --- 2. ANALISI DEL PERCORSO (CARTELLA PER CARTELLA) ---
        relative_path = os.path.relpath(root_path, base_dir)
        parts = relative_path.split(os.sep)

        # A. Solver e SimType dalla radice (es. ILP_sim_SystemAP5 o DTS-base_sim...)
        if len(parts) > 0:
            if '_sim_' in parts[0]:  # Cerca il pattern standard
                solver_part, sim_type_part = parts[0].split('_sim_', 1)
                if params['solver'] is None:
                    # Questo cattura "DTS-base", "OrbitAware", "ILP", etc.
                    params['solver'] = solver_part
                params['sim_type'] = sim_type_part
            elif '_' in parts[0]:  # Fallback per vecchi nomi
                solver_part, sim_type_part = parts[0].split('_', 1)
                if params['solver'] is None:
                    params['solver'] = solver_part
                params['sim_type'] = sim_type_part

        # B. Scansione parti intermedie per varianti ILP
        for part in parts:
            part_lower = part.lower()

            # --- ILP Variants ---
            if part_lower == 'hierarchical':
                params['ilp_variant'] = 'Hierarchical'
            elif part_lower == 'weighted':
                params['ilp_variant'] = 'Weighted'

            # --- ILP Hierarchical Goals ---
            elif part_lower == 'energy':
                params['opt_goal'] = 'Energy'
            elif part_lower == 'time':
                params['opt_goal'] = 'Time'

            # --- ILP Weighted Weights ---
            # Cartella tipo: w_e_0.3_w_R_0.7
            elif part_lower.startswith('w_e_'):
                w_match = re.search(r'w_e_([\d\.]+)_w_?r_([\d\.]+)', part_lower)
                if w_match:
                    params['w_e'] = float(w_match.group(1))
                    params['w_R'] = float(w_match.group(2))

            # --- Parametri Standard ---
            elif part_lower.startswith('seed_'):
                params['seed'] = part[len('seed_'):]
            elif part_lower.startswith('routing_'):
                params['routing'] = part[len('routing_'):]
            elif part_lower.startswith('img_res_'):
                params['img_res'] = part[len('img_res_'):]
            elif part_lower.startswith('deadline_'):
                params['deadline'] = part[len('deadline_'):]
            elif part_lower.startswith('energy_budget_'):
                raw = part[len('energy_budget_'):]
                try:
                    params['energy_budget'] = float(raw)
                except Exception:
                    params['energy_budget'] = raw

        # --- 3. RINOMINA FINALE DEL SOLVER ---
        # Qui creiamo le etichette esatte richieste

        base_solver = params.get('solver', 'Unknown')

        if base_solver == 'ILP':
            variant = params.get('ilp_variant')
            goal = params.get('opt_goal')
            we = params.get('w_e')
            wr = params.get('w_R')

            # Caso 1: Hierarchical
            if variant == 'Hierarchical':
                if goal:
                    # Output: "ILP Hierarchical (Energy)" oppure "(Time)"
                    params['solver'] = f"ILP Hierarchical ({goal})"
                else:
                    params['solver'] = "ILP Hierarchical"  # Fallback se manca cartella energy/time

            # Caso 2: Weighted
            elif variant == 'Weighted':
                if we is not None and wr is not None:
                    # Output: "ILP (we=0.3, wR=0.7)"
                    # Nota: uso un formato stringa pulito.
                    # Lo script di plot potrà usarlo direttamente o aggiungere LaTeX se vuole.
                    params['solver'] = f"ILP (we={we}, wR={wr})"
                else:
                    # Se siamo in weighted ma non ha letto i pesi (errore regex?), resta ILP
                    pass

    except Exception as e:
        print(f"  [Errore Estrazione Parametri] per {filename}: {e}")

    return params

BASE_DIR_pc = r"C:\Users\danil\PycharmProjects\SECMotionModel\result"
def merge_simulation_data(base_dir):
    """
    Funzione principale per scansionare, estrarre parametri e unire i CSV.
    Salva i risultati nella cartella SUPERIORE a base_dir.
    """

    # Rimuove l'eventuale slash finale per sicurezza e prende la directory genitore
    parent_dir = os.path.dirname(BASE_DIR_pc.rstrip(os.sep))
    output_dir = os.path.join(parent_dir, "MERGED_DATA")
    # --------------------------------------

    os.makedirs(output_dir, exist_ok=True)
    print(f"Avvio scansione ricorsiva di: {base_dir}")
    print(f"I file unificati verranno salvati in: {output_dir}\n")

    # Liste per contenere i DataFrame
    all_dfs = {
        "Results": [],
        "Energy": [],
        "Routing_Log": [],
        "Summary": []
    }

    # Colonne attese per il file Summary per evitare errori di parsing
    expected_cols = {
        "Summary": ['Seed', 'TotalTasks', 'Arrived', 'Expired', 'OutOfBuff', 'OnSim']
    }

    for root, dirs, files in os.walk(base_dir, topdown=True):
        # Controllo di sicurezza: anche se ora è fuori, previene loop se output_dir fosse dentro
        if output_dir in root:
            continue

        for filename in files:
            file_path = os.path.join(root, filename)

            file_type = None
            if filename.startswith('results_') and filename.endswith('.csv'):
                file_type = "Results"
            elif filename.startswith('energy_stats_') and filename.endswith('.csv'):
                file_type = "Energy"
            elif filename.startswith('[') and '_AT_' in filename and filename.endswith('.csv'):
                file_type = "Routing_Log"
            elif filename.startswith('SUMMARY[') and filename.endswith('.csv'):
                file_type = "Summary"

            if file_type:
                # print(f"[{file_type}] Trovato: {filename}")
                params = extract_parameters_from_path(root, filename, base_dir)

                try:
                    cols_to_use = expected_cols.get(file_type, None)
                    df = pd.read_csv(file_path, usecols=cols_to_use)

                    # --- FILTRO STATUS "In Queue" ---
                    if 'Status' in df.columns:
                        initial_len = len(df)
                        # Rimuove le righe dove Status è esattamente "In Queue"
                        df = df[df['Status'].astype(str).str.strip() != "In Queue"]

                        filtered_len = len(df)
                        diff = initial_len - filtered_len
                        if diff > 0:
                            print(f"  -> Filtrate {diff} righe 'In Queue' da {filename}")
                    # --------------------------------

                    # Aggiunge tutti i parametri estratti come nuove colonne
                    for k, v in params.items():
                        df[k] = v

                    all_dfs[file_type].append(df)

                except pd.errors.EmptyDataError:
                    print(f"  [Attenzione] File vuoto: {filename}, file saltato.")
                except Exception as e:
                    print(f"  [Errore Lettura] {filename}: {e}, file saltato.")

    print("\n--- Scansione completata. Avvio unione dei dati... ---")

    # Dizionario per i nomi dei file di output
    output_filenames = {
        "Results": "MERGED_all_results.csv",
        "Energy": "MERGED_all_energy_stats.csv",
        "Routing_Log": "MERGED_all_routing_logs.csv",
        "Summary": "MERGED_all_summaries.csv"
    }

    # Unisci e salva tutti i file trovati
    for file_type, dfs_list in all_dfs.items():
        if dfs_list:
            print(f"Sto unendo {len(dfs_list)} file per {file_type}...")
            master_df = pd.concat(dfs_list, ignore_index=True)

            # Gestione duplicato colonna 'seed'
            if 'Seed' in master_df.columns and 'seed' in master_df.columns:
                master_df = master_df.drop(columns=['Seed'])

            if file_type == "Summary":
                cols_to_drop = ['AT', 'CPU']
                master_df = master_df.drop(columns=cols_to_drop, errors='ignore')

            save_path = os.path.join(output_dir, output_filenames[file_type])
            master_df.to_csv(save_path, index=False)
            print(f"✔️  File '{output_filenames[file_type]}' unificato salvato in: {save_path} ({len(master_df)} righe)")

    print("\n--- Processo di unione completato. ---")


# --- Esecuzione ---
if __name__ == "__main__":
    # Assicurati che il percorso sia corretto
    BASE_DIR_pc = r"C:\Users\danil\PycharmProjects\SECMotionModel\result"
    BASE_DIR = r"G:\Drive condivisi\DTS - ILP results\SIMULATION_RESULT\V0\result"

    if os.path.exists(BASE_DIR):
        merge_simulation_data(BASE_DIR)
    else:
        print(f"Errore: La cartella {BASE_DIR} non esiste.")