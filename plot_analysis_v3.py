import os
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import textwrap
import traceback

# ----------------------------------------------------------------------
# CONFIGURAZIONE
# ----------------------------------------------------------------------

BASE_DIR = "MERGED_DATA"
OUTPUT_DIR = "ANALYSIS_PLOTS_V9"
os.makedirs(OUTPUT_DIR, exist_ok=True)

FILE_PATHS = {
    "results": os.path.join(BASE_DIR, "MERGED_all_results.csv"),
    "energy": os.path.join(BASE_DIR, "MERGED_all_energy_stats.csv")
}

# Filtri globali
GLOBAL_FILTERS = {
    'deadline': 10,
    # 'energy_budget': 100000,
}

# Tasso di arrivo target per i grafici vs Energy Budget
TARGET_AT = 10.0

# Impostazioni grafiche
sns.set_theme(style="whitegrid")
GLOBAL_COLOR_MAP = {}  # Verrà riempita nel main

print(f"Script di analisi V9 (Bande Colorate + Label 'Algorithm') avviato.")
print(f"Grafici salvati in: {os.path.abspath(OUTPUT_DIR)}")

# ==============================================================================
# CONFIGURAZIONE STILI
# ==============================================================================
MARKER_LS_CONFIG = {
    "DTS-base": {"marker": "X", "ls": "--"},
    "DTS-APopt": {"marker": "o", "ls": "-"},
    "OrbitAware": {"marker": "D", "ls": "-."},
    "ILP Hierarchical (Energy)": {"marker": "s", "ls": ":"},
    "ILP Hierarchical (Time)": {"marker": "*", "ls": ":"},
    "ILP Hierarchical": {"marker": "s", "ls": ":"},
    "ILP (we=0.7, wR=0.3)": {"marker": "^", "ls": "-."},
    "ILP (we=0.3, wR=0.7)": {"marker": "v", "ls": "-."},
    "ILP (we=0.5, wR=0.5)": {"marker": "p", "ls": "-."}
}
FALLBACK_STRUCT = {"marker": "h", "ls": "-"}

# ==============================================================================
# ORDINE DI VISUALIZZAZIONE NELLA LEGENDA
# ==============================================================================
# I nomi devono coincidere ESATTAMENTE con quelli nel DataFrame
LEGEND_ORDER = [
    "DTS-base",
    "DTS-APopt",
    "OrbitAware",
    "ILP Hierarchical (Time)",
    "ILP Hierarchical (Energy)",
    "ILP (we=0.7, wR=0.3)",
    "ILP (we=0.5, wR=0.5)",
    "ILP (we=0.3, wR=0.7)"
]

# ----------------------------------------------------------------------
# FUNZIONI HELPER
# ----------------------------------------------------------------------

def disambiguate_algorithms(df):
    """
    Rinomina le varianti ILP basandosi sui pesi, agendo sulla colonna 'Algorithm'.
    """
    if df is None or df.empty: return df

    # Assicuriamoci di lavorare sulla colonna corretta
    if 'Algorithm' not in df.columns:
        return df

    if 'w_e' in df.columns and 'w_R' in df.columns:
        # Cerca righe dove Algorithm è 'ILP' e i pesi sono definiti
        mask = (df['Algorithm'] == 'ILP') & (df['w_e'].notna()) & (df['w_R'].notna())
        if mask.any():
            print("  Disambiguazione ILP (Pesi) in corso...")
            df.loc[mask, 'Algorithm'] = df.loc[mask].apply(
                lambda row: f"ILP ($w_e$={row['w_e']}, $w_R$={row['w_R']})", axis=1
            )
    return df


def apply_filters(df, filters):
    if df is None or df.empty: return pd.DataFrame()
    df_filtered = df.copy()
    for col, value in filters.items():
        if value is not None and col in df_filtered.columns:
            try:
                col_type = df_filtered[col].dtype
                typed_value = pd.Series([value]).astype(col_type).iloc[0]
                mask = (df_filtered[col] == typed_value)
            except Exception:
                mask = (df_filtered[col] == value)
            df_filtered = df_filtered[mask]
    return df_filtered


def save_and_close(fig, path):
    try:
        # Rimesso dpi default per dimensioni standard
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        print(f"  ✅ Salvato: {os.path.basename(path)}")
    except Exception as e:
        print(f"  [Errore Salvataggio] {e}")


# ==============================================================================
# FUNZIONE DI PLOTTING
# ==============================================================================

def create_line_plot_with_errors(
        df_stats, x_col, y_col_mean, y_col_error,
        x_label, y_label, filename, legend_title="Algorithms",
        use_precomputed_errors=False
):
    try:
        fig, ax = plt.subplots(figsize=(10, 6))

        df_stats = df_stats.copy()
        df_stats[x_col] = pd.to_numeric(df_stats[x_col], errors='coerce')
        df_stats = df_stats.sort_values(by=[x_col])

        # Qui usiamo 'Algorithm'
        df_stats['Algorithms'] = df_stats['Algorithm']

        # Asse X Equidistante
        x_levels = sorted(df_stats[x_col].unique())
        x_map = {val: i for i, val in enumerate(x_levels)}
        df_stats['x_plot_index'] = df_stats[x_col].map(x_map)

        # --- CALCOLO ORDINE LEGENDA DINAMICO ---
        # 1. Troviamo gli algoritmi presenti in QUESTO set di dati
        present_algos = set(df_stats['Algorithms'].unique())

        # 2. Creiamo la lista ordinata filtrando LEGEND_ORDER
        # Mantiene l'ordine preferito solo per quelli presenti
        plot_order = [algo for algo in LEGEND_ORDER if algo in present_algos]

        # 3. Se c'è qualche algoritmo nuovo non in lista, aggiungilo in fondo
        for algo in present_algos:
            if algo not in plot_order:
                plot_order.append(algo)
        # ---------------------------------------

        unique_algorithms = sorted(df_stats['Algorithms'].unique())

        # === MODALITÀ SEABORN (Bande Colorate) ===
        markers_dict = {}
        dashes_dict = {}
        style_map_seaborn = {'-': "", '--': (2, 2), ':': (1, 1), '-.': (3, 1, 1, 1)}

        for alg in unique_algorithms:
            s = MARKER_LS_CONFIG.get(alg, FALLBACK_STRUCT)
            markers_dict[alg] = s['marker']
            dashes_dict[alg] = style_map_seaborn.get(s['ls'], "")

        sns.lineplot(
            data=df_stats,
            x='x_plot_index',
            y=y_col_mean,
            hue='Algorithms',
            style='Algorithms',
            hue_order=plot_order,  # <--- QUI IMPONIAMO L'ORDINE COLORI/LEGENDA
            style_order=plot_order,  # <--- QUI IMPONIAMO L'ORDINE STILI
            palette=GLOBAL_COLOR_MAP,
            markers=markers_dict,
            dashes=dashes_dict,
            markersize=11,
            lw=2.5,
            ax=ax,
            estimator='mean',
            errorbar= 'se',
            err_style='band'
        )

        # Ripristino Etichette X
        ax.set_xticks(range(len(x_levels)))
        cleaned_labels = []
        for val in x_levels:
            if isinstance(val, (int, float)):
                f_val = float(val)
                if f_val.is_integer():
                    cleaned_labels.append(str(int(f_val)))
                else:
                    cleaned_labels.append(str(val))
            else:
                cleaned_labels.append(str(val))
        ax.set_xticklabels(cleaned_labels)

        ax.set_xlabel(x_label, fontsize=14)
        ax.set_ylabel(y_label, fontsize=14)
        ax.grid(True, linestyle='--', alpha=0.6)
        ax.set_ylim(bottom=0)
        current_max_y = ax.get_ylim()[1]
        ax.set_ylim(top=current_max_y * 1.05)
        if legend_title: ax.legend(title=legend_title)

        save_and_close(fig, os.path.join(OUTPUT_DIR, filename))

    except Exception as e:
        print(f"  [Errore] Creazione grafico {filename}: {e}")
        traceback.print_exc()


# ----------------------------------------------------------------------
# PRE-PROCESSING (PER-SEED)
# ----------------------------------------------------------------------
def preprocess_data(results_df, energy_df):
    print("Avvio pre-processing dei dati...")

    # Mappatura AT
    at_map = {0.5: 2, 0.25: 4, 0.166: 6, 0.16: 6, 0.125: 8, 0.1: 10}

    def map_at(df):
        if 'AT' in df.columns:
            df['AT_rounded'] = df['AT'].round(3)
            at_map_rounded = {round(k, 3): v for k, v in at_map.items()}
            df['Arrival Rate'] = df['AT_rounded'].map(at_map_rounded)
            if df['Arrival Rate'].isna().any():
                df['Arrival Rate'] = df['Arrival Rate'].fillna(1 / pd.to_numeric(df['AT'], errors='coerce'))
            df = df.drop(columns=['AT_rounded'])
        return df

    results_df = map_at(results_df)
    energy_df = map_at(energy_df)

    # Nota: Usiamo 'Algorithm' al posto di 'solver'
    experiment_cols = ['Algorithm', 'Arrival Rate', 'energy_budget']

    # --- 1. Success Rate ---
    df_sr_per_seed = pd.DataFrame()
    if 'Status' in results_df.columns:
        df_sr_per_seed = results_df.groupby(experiment_cols + ['seed'])['Status'].apply(
            lambda x: (x == 'Completed').mean()
        ).reset_index(name='Success Rate')

        # --- 2. Energy per Task ---
    df_energy_per_task_seed = pd.DataFrame()
    if 'Energy_TOTAL [J]' in results_df.columns:
        df_agg = results_df.groupby(experiment_cols + ['seed']).agg(
            Total_Energy_Consumed=('Energy_TOTAL [J]', 'sum'),
            Num_Completed_Tasks=('Status', lambda x: (x == 'Completed').sum())
        )
        df_agg['Energy per Task'] = df_agg['Total_Energy_Consumed'] / df_agg['Num_Completed_Tasks']
        df_agg.replace([np.inf, -np.inf], 0, inplace=True)
        df_energy_per_task_seed = df_agg.reset_index()

    # --- 3. Energy per SEN ---
    df_energy_per_sen_seed = pd.DataFrame()
    if 'Total_Energy_Consumed_J' in energy_df.columns:
        energy_df['is_Used'] = (energy_df['Total_Tasks_Completed'] > 0).astype(int)
        df_sen_agg = energy_df.groupby(experiment_cols + ['seed']).agg(
            Total_Energy_Consumed=('Total_Energy_Consumed_J', 'sum'),
            Num_Used_Servers=('is_Used', 'sum')
        )
        df_sen_agg['Energy per SEN'] = df_sen_agg['Total_Energy_Consumed'] / df_sen_agg['Num_Used_Servers']
        df_sen_agg.replace([np.inf, -np.inf], 0, inplace=True)
        df_energy_per_sen_seed = df_sen_agg.reset_index()

    # --- 4. Response Time ---
    df_response_time_seed = pd.DataFrame()
    df_completed = results_df[results_df['Status'] == 'Completed'].copy()
    if 'Time in system' in df_completed.columns:
        df_response_time_seed = df_completed.groupby(experiment_cols + ['seed'])['Time in system'].agg(
            Avg='mean',
            P95=lambda x: x.quantile(0.95)
        ).reset_index()

    # --- 5. Routing Time ---
    df_routing_time_seed = pd.DataFrame()
    if 'Routing Duration' in results_df.columns:
        df_routing_time_seed = results_df.groupby(experiment_cols + ['seed'])['Routing Duration'].agg(
            Avg='mean',
            P95=lambda x: x.quantile(0.95)
        ).reset_index()

    # --- 6. Hops ---
    df_hops_seed = pd.DataFrame()
    if 'Num Hops Routing' in results_df.columns and 'Num Hops' in results_df.columns:
        df_hops_agg = results_df.groupby(experiment_cols + ['seed']).agg(
            Routing_Avg=('Num Hops Routing', 'mean'),
            Routing_Max=('Num Hops Routing', 'max'),
        ).reset_index()
        df_hops_agg_exec = df_completed.groupby(experiment_cols + ['seed']).agg(
            Exec_Avg=('Num Hops', 'mean'),
        ).reset_index()
        df_hops_seed = pd.merge(df_hops_agg, df_hops_agg_exec, on=experiment_cols + ['seed'])

    return results_df, df_sr_per_seed, df_energy_per_task_seed, df_energy_per_sen_seed, df_response_time_seed, df_routing_time_seed, df_hops_seed


def preprocess_data_load_balancing(energy_df):
    if 'Total_Tasks_Completed' not in energy_df.columns: return pd.DataFrame()
    experiment_cols = ['Algorithm', 'Arrival Rate', 'energy_budget', 'seed']
    df_agg = energy_df.groupby(experiment_cols)['Total_Tasks_Completed'].agg(
        Avg_Load='mean', Max_Load='max'
    ).reset_index()
    df_agg['Load Balancing Degree'] = (df_agg['Avg_Load'] / df_agg['Max_Load']).fillna(0)
    return df_agg


# ----------------------------------------------------------------------
# PLOT ROUTINES
# ----------------------------------------------------------------------

def plot_1_success_rate(df_stats):
    print("Generazione grafici 1.x: Success Rate")

    if 'Success Rate' in df_stats.columns:
        create_line_plot_with_errors(
            df_stats, 'Arrival Rate', 'Success Rate', None,
            'Arrival Rate (req/s)', 'Success Rate (%)',
            "1.1_success_rate_vs_arrival_rate.png"
        )

        df_filtered = df_stats[np.isclose(df_stats['Arrival Rate'], TARGET_AT)]
        if not df_filtered.empty:
            create_line_plot_with_errors(
                df_filtered, 'energy_budget', 'Success Rate', None,
                'Energy Budget (J)', f'Success Rate (%) {int(TARGET_AT)} req/s',
                "1.2_success_rate_vs_energy_budget.png"
            )


'''def plot_2_energy(df_energy_per_task, df_energy_per_sen, df_sr_seed=None):
    print("Generazione grafici 2.x: Energy")

    # Tabella
    if df_sr_seed is not None and not df_energy_per_task.empty:
        try:
            df_en_mean = df_energy_per_task.groupby(['Algorithm', 'Arrival Rate', 'energy_budget'])[
                'Energy per Task'].mean().reset_index()
            df_sr_mean = df_sr_seed.groupby(['Algorithm', 'Arrival Rate', 'energy_budget'])[
                'Success Rate'].mean().reset_index()

            merged = pd.merge(df_en_mean, df_sr_mean, on=['Algorithm', 'Arrival Rate', 'energy_budget'])
            merged['E_real_avg'] = merged['Energy per Task'] * merged['Success Rate']

            subset_10 = merged[merged['Arrival Rate'] == 10.0].copy()
            if not subset_10.empty:
                summary = subset_10.groupby('Algorithm').mean(numeric_only=True).reset_index()
                print("\n" + "=" * 85)
                print(f"{'TABELLA ENERGIA (AT=10)':^85}")
                print("-" * 85)
                for _, row in summary.iterrows():
                    print(
                        f"{row['Algorithm']:<25} | E_Task: {row['Energy per Task']:.2f} J | SR: {row['Success Rate'] * 100:.1f}% | E_Real: {row['E_real_avg']:.2f} J")
                print("=" * 85 + "\n")
        except Exception as e:
            print(f"Err Tabella: {e}")

    # Plot
    if 'Energy per Task' in df_energy_per_task.columns:
        create_line_plot_with_errors(df_energy_per_task, 'Arrival Rate', 'Energy per Task', None,
                                     'Arrival Rate (req/s)', 'Average Energy per Task (J)',
                                     "2.1_energy_per_task_vs_arrival_rate.png")

    if 'Energy per SEN' in df_energy_per_sen.columns:
        create_line_plot_with_errors(df_energy_per_sen, 'Arrival Rate', 'Energy per SEN', None,
                                     'Arrival Rate (req/s)', 'Average Energy per Used SEN (J)',
                                     "2.3_energy_per_sen_vs_arrival_rate.png")

    if 'Energy per SEN' in df_energy_per_sen.columns:
        df_filt = df_energy_per_sen[np.isclose(df_energy_per_sen['Arrival Rate'], TARGET_AT)]
        create_line_plot_with_errors(df_filt, 'energy_budget', 'Energy per SEN', None,
                                     'Energy Budget (J)', f'Avg Energy per SEN (J) {int(TARGET_AT)} req/s',
                                     "2.4_energy_per_sen_vs_energy_budget.png")
'''

def plot_2_energy(df_energy_per_task, df_energy_per_sen, df_sr_seed=None):
    print("Generazione grafici 2.x: Energy")

    if df_sr_seed is not None and not df_energy_per_task.empty:
        try:
            df_en_mean = df_energy_per_task.groupby(['Algorithm', 'Arrival Rate', 'energy_budget'])[
                'Energy per Task'].mean().reset_index()
            df_sr_mean = df_sr_seed.groupby(['Algorithm', 'Arrival Rate', 'energy_budget'])[
                'Success Rate'].mean().reset_index()
            merged = pd.merge(df_en_mean, df_sr_mean, on=['Algorithm', 'Arrival Rate', 'energy_budget'])
            merged['E_real_avg'] = merged['Energy per Task'] * merged['Success Rate']
            subset_10 = merged[merged['Arrival Rate'] == 10.0].copy()
            if not subset_10.empty:
                summary = subset_10.groupby('Algorithm').mean(numeric_only=True).reset_index()
                print("\n" + "=" * 85)
                print(f"{'TABELLA ENERGIA (AT=10)':^85}")
                print("-" * 85)
                for _, row in summary.iterrows():
                    print(
                        f"{row['Algorithm']:<25} | E_Task: {row['Energy per Task']:.2f} J | SR: {row['Success Rate'] * 100:.1f}% | E_Real: {row['E_real_avg']:.2f} J")
                print("=" * 85 + "\n")
        except Exception as e:
            print(f"Err Tabella: {e}")

    if 'Energy per Task' in df_energy_per_task.columns:
        create_line_plot_with_errors(df_energy_per_task, 'Arrival Rate', 'Energy per Task', None,
                                     'Arrival Rate (req/s)', 'Average Energy per Service (J)',
                                     "2.1_energy_per_task_vs_arrival_rate.png")

    if 'Energy per SEN' in df_energy_per_sen.columns:
        create_line_plot_with_errors(df_energy_per_sen, 'Arrival Rate', 'Energy per SEN', None,
                                     'Arrival Rate (req/s)', 'Average Energy per Used SEN (J)',
                                     "2.3_energy_per_sen_vs_arrival_rate.png")

    if 'Energy per SEN' in df_energy_per_sen.columns:
        df_filt = df_energy_per_sen[np.isclose(df_energy_per_sen['Arrival Rate'], TARGET_AT)]
        create_line_plot_with_errors(df_filt, 'energy_budget', 'Energy per SEN', None,
                                     'Energy Budget (J)', f'Avg Energy per SEN (J) {int(TARGET_AT)} req/s',
                                     "2.4_energy_per_sen_vs_energy_budget.png")

def plot_3_response_time(df_stats):
    print("Generazione grafici 3.x: Response Time")
    create_line_plot_with_errors(df_stats, 'Arrival Rate', 'Avg', None, 'Arrival Rate (req/s)', 'AVG Response Time (s)',
                                 '3.1_response_time_AVG_vs_arrival_rate.png')
    create_line_plot_with_errors(df_stats, 'Arrival Rate', 'P95', None, 'Arrival Rate (req/s)', 'P95 Response Time (s)',
                                 '3.3_response_time_P95_vs_arrival_rate.png')

    df_filt = df_stats[np.isclose(df_stats['Arrival Rate'], TARGET_AT)]
    if not df_filt.empty:
        create_line_plot_with_errors(df_filt, 'energy_budget', 'Avg', None, 'Energy Budget (J)',
                                     f'AVG Response Time (s) {int(TARGET_AT)} req/s',
                                     '3.4_response_time_AVG_vs_energy_budget.png')
        create_line_plot_with_errors(df_filt, 'energy_budget', 'P95', None, 'Energy Budget (J)',
                                     f'P95 Response Time (s) {int(TARGET_AT)} req/s',
                                     '3.6_response_time_P95_vs_energy_budget.png')


def plot_4_routing_time(df_stats):
    print("Generazione grafici 4.x: Routing Time")
    create_line_plot_with_errors(df_stats, 'Arrival Rate', 'Avg', None, 'Arrival Rate (req/s)', 'AVG Routing Time (s)',
                                 '4.1_routing_time_AVG_vs_arrival_rate.png')
    create_line_plot_with_errors(df_stats, 'Arrival Rate', 'P95', None, 'Arrival Rate (req/s)', 'P95 Routing Time (s)',
                                 '4.3_routing_time_P95_vs_arrival_rate.png')

    df_filt = df_stats[np.isclose(df_stats['Arrival Rate'], TARGET_AT)]
    if not df_filt.empty:
        create_line_plot_with_errors(df_filt, 'energy_budget', 'Avg', None, 'Energy Budget (J)',
                                     f'AVG Routing Time {int(TARGET_AT)} req/s',
                                     '4.4_routing_time_AVG_vs_energy_budget.png')
        create_line_plot_with_errors(df_filt, 'energy_budget', 'P95', None, 'Energy Budget (J)',
                                     f'P95 Routing Time {int(TARGET_AT)} req/s',
                                     '4.6_routing_time_P95_vs_energy_budget.png')


def plot_5_hops(df_stats):
    print("Generazione grafici 5.x: Hops")
    create_line_plot_with_errors(df_stats, 'Arrival Rate', 'Routing_Avg', None, 'Arrival Rate (req/s)',
                                 'AVG Hops (Routing)', '5.1_routing_hops_AVG_vs_arrival_rate.png')
    create_line_plot_with_errors(df_stats, 'Arrival Rate', 'Routing_Max', None, 'Arrival Rate (req/s)',
                                 'MAX Hops (Routing)', '5.2_routing_hops_MAX_vs_arrival_rate.png')
    create_line_plot_with_errors(df_stats, 'Arrival Rate', 'Exec_Avg', None, 'Arrival Rate (req/s)',
                                 'AVG Hops (Executed)', '5.4_executed_hops_AVG_vs_arrival_rate.png')

    df_filt = df_stats[np.isclose(df_stats['Arrival Rate'], TARGET_AT)]
    if not df_filt.empty:
        create_line_plot_with_errors(df_filt, 'energy_budget', 'Routing_Avg', None, 'Energy Budget (J)',
                                     'AVG Hops (Routing)', '5.5_routing_hops_AVG_vs_energy_budget.png')
        create_line_plot_with_errors(df_filt, 'energy_budget', 'Exec_Avg', None, 'Energy Budget (J)',
                                     'AVG Hops (Executed)', '5.6_executed_hops_AVG_vs_energy_budget.png')


def plot_8_load_balancing_degree(df_stats):
    print("Generazione grafici 8.x: Load Balancing")
    create_line_plot_with_errors(df_stats, 'Arrival Rate', 'Load Balancing Degree', None, 'Arrival Rate (req/s)',
                                 'Dynamic Load Balancing Degree (Ratio)', "8.1_load_balancing_degree_vs_arrival_rate.png")

    df_filt = df_stats[np.isclose(df_stats['Arrival Rate'], TARGET_AT)]
    df_filt = df_filt[df_filt['energy_budget'].notna()]
    if not df_filt.empty:
        create_line_plot_with_errors(df_filt, 'energy_budget', 'Load Balancing Degree', None, 'Energy Budget (J)',
                                     f'Dynamic Load Balancing Degree (Ratio)  {int(TARGET_AT)} req/s', "8.2_load_balancing_degree_vs_energy_budget.png")


# --- Special Plots (Heatmap/Bar) ---
def plot_6_1_failure_reason(df_results, arrival_rates_to_plot):
    print(f"Generazione grafico: 6.0 Motivo Fallimenti")
    try:
        df_failed = df_results[(df_results['Status'] != 'Completed') & (df_results['Rejection Reason'].notna())].copy()
        df_failed['Rejection Reason'] = df_failed['Rejection Reason'].str.strip()
        df_filtered = df_failed[df_failed['Arrival Rate'].isin(arrival_rates_to_plot)]
        if df_filtered.empty: return
        df_agg = df_filtered.groupby(['Algorithm', 'Arrival Rate', 'Rejection Reason', 'seed']).size().reset_index(
            name='Count')
        df_stats = df_agg.groupby(['Algorithm', 'Arrival Rate', 'Rejection Reason'])['Count'].mean().reset_index()
        for at in arrival_rates_to_plot:
            df_plot = df_stats[df_stats['Arrival Rate'] == at]
            if df_plot.empty: continue
            df_total = df_plot.groupby('Algorithm')['Count'].sum().reset_index(name='Count_Total')
            df_plot = pd.merge(df_plot, df_total, on='Algorithm')
            df_plot['Percentage'] = (df_plot['Count'] / df_plot['Count_Total']) * 100
            df_pivot = df_plot.pivot(index='Algorithm', columns='Rejection Reason', values='Percentage').fillna(0)
            fig, ax = plt.subplots(figsize=(11, 7))
            df_pivot.plot(kind='bar', stacked=True, ax=ax, cmap='Set3', width=0.5)
            ax.set_xlabel('Algorithms', fontsize=12)
            ax.set_ylabel('Rejection Reasons (%)', fontsize=12)
            ax.legend(title=None, loc='best')
            ax.set_ylim(0, 100)
            new_labels = ['\n'.join(textwrap.wrap(l.get_text(), 12)) for l in ax.get_xticklabels()]
            ax.set_xticklabels(new_labels, rotation=0)
            save_and_close(fig, os.path.join(OUTPUT_DIR, f"6.0_failure_reason_AT_{at}_PERCENT.png"))
    except Exception as e:
        print(f"Err plot_6: {e}")


def plot_7_load_balancing_heatmaps(df_energy, filters, output_dir, algorithms_to_include=None):
    print("Generazione grafici 7.x: Heatmap Bilanciamento Carico")
    try:
        df = apply_filters(df_energy, filters)
        if df.empty or 'Total_Tasks_Completed' not in df.columns: return

        # Filtro Algorithms
        if algorithms_to_include: df = df[df['Algorithm'].isin(algorithms_to_include)]

        df['Rank'] = df.groupby(['Algorithm', 'seed', 'Arrival Rate', 'energy_budget'])['Total_Tasks_Completed'].rank(
            method='first', ascending=False)
        df_top15 = df[df['Rank'] <= 15].copy()

        # 7.1
        df_stats_at = df_top15.groupby(['Algorithm', 'Rank', 'Arrival Rate'])[
            'Total_Tasks_Completed'].mean().reset_index()

        def draw_at(data, **k):
            p = data.pivot(index='Rank', columns='Arrival Rate', values='Total_Tasks_Completed').fillna(0).sort_index()
            p.index = [f'SEN {int(r)}' for r in p.index]
            sns.heatmap(p, annot=False, fmt=".0f", cmap="viridis", annot_kws={"size": 10}, **k)

        g = sns.FacetGrid(df_stats_at, col='Algorithm', col_wrap=2, height=7, aspect=1.2)
        g.map_dataframe(draw_at, cbar_kws={'label': 'Mean Tasks'})
        save_and_close(g.figure, os.path.join(output_dir, "7.1_heatmap_load_vs_arrival_rate.png"))

        # 7.2
        df_stats_eb = df_top15.groupby(['Algorithm', 'Rank', 'energy_budget'])[
            'Total_Tasks_Completed'].mean().reset_index()

        def draw_eb(data, **k):
            p = data.pivot(index='Rank', columns='energy_budget', values='Total_Tasks_Completed').fillna(0).sort_index()
            p.index = [f'SEN {int(r)}' for r in p.index]
            sns.heatmap(p, annot=False, fmt=".0f", cmap="viridis", annot_kws={"size": 10}, **k)

        g = sns.FacetGrid(df_stats_eb, col='Algorithm', col_wrap=2, height=7, aspect=1.2)
        g.map_dataframe(draw_eb, cbar_kws={'label': 'Mean Tasks'})
        save_and_close(g.figure, os.path.join(output_dir, "7.2_heatmap_load_vs_energy_budget.png"))
    except Exception as e:
        print(f"Err plot_7: {e}")


def plot_7_3_aggregated_rank_heatmap(df_energy, filters, output_dir, algorithms_to_include=None):
    print("Generazione grafico: 7.3 Heatmap Aggregata (SOLO Arrival Rate = 10)")
    try:
        df = apply_filters(df_energy, filters)
        target_at = 10.0
        if 'Arrival Rate' not in df.columns: return
        df = df[np.isclose(df['Arrival Rate'], target_at)]
        if df.empty: return

        # Filtro Algorithms
        if algorithms_to_include: df = df[df['Algorithm'].isin(algorithms_to_include)]

        df['Rank'] = df.groupby(['Algorithm', 'seed', 'energy_budget'])['Total_Tasks_Completed'].rank(method='first',
                                                                                                      ascending=False)
        df_top15 = df[df['Rank'] <= 15].copy()
        df_agg = df_top15.groupby(['Algorithm', 'Rank'])['Total_Tasks_Completed'].mean().reset_index()
        df_heatmap = df_agg.pivot(index='Rank', columns='Algorithm', values='Total_Tasks_Completed').fillna(0)
        df_heatmap = df_heatmap.sort_index()
        df_heatmap.index = [f'SEN {int(r)}' for r in df_heatmap.index]
        fig, ax = plt.subplots(figsize=(10, 12))
        sns.heatmap(df_heatmap, annot=False, fmt=".1f", cmap="viridis", linewidths=.5, ax=ax, annot_kws={"size": 11},
                    cbar_kws={'label': 'Avg Services Completed (10 req/s)'})
        ax.set_xlabel('Algorithms', fontsize=14);
        ax.set_ylabel('SEN Rank (1=Most Busy)', fontsize=14)
        save_and_close(fig, os.path.join(output_dir, "7.3_heatmap_aggregated_rank_AT_10.png"))
    except Exception as e:
        print(f"Err plot_7_3: {e}")


def plot_load_balancing_heatmap_V2(df_energy, filters, output_dir):
    print("Generazione grafico: 7.0 Heatmap Bilanciamento per Server Reale")
    try:
        df = apply_filters(df_energy, filters)
        col_srv = 'Server_Name' if 'Server_Name' in df.columns else 'Server Name'
        if df.empty or col_srv not in df.columns: return
        df_agg = df.groupby(['Algorithm', col_srv])['Total_Tasks_Completed'].mean().reset_index()
        top_list = df_agg.groupby(col_srv)['Total_Tasks_Completed'].sum().nlargest(15).index.tolist()
        df_top = df_agg[df_agg[col_srv].isin(top_list)]
        df_hm = df_top.pivot(index=col_srv, columns='Algorithm', values='Total_Tasks_Completed').fillna(0).loc[top_list]
        fig, ax = plt.subplots(figsize=(10, 14))
        sns.heatmap(df_hm, annot=False, fmt=".1f", cmap="viridis", linewidths=.5, ax=ax, annot_kws={"size": 10},
                    cbar_kws={'label': 'Avg Services Completed'})
        ax.set_xlabel('Algorithms', fontsize=14);
        ax.set_ylabel('Server Name', fontsize=14)
        save_and_close(fig, os.path.join(output_dir, "7.0_load_balancing_heatmap_by_server.png"))
    except Exception as e:
        print(f"Err plot_7_0: {e}")


def generate_full_report(df_results, df_sr_seed, df_resp_seed, df_rout_seed, df_hops_seed, df_en_task_seed, df_lb_seed,
                         output_dir):
    """
    Genera un report testuale completo (Console + File TXT).
    Include Success Rate, Tempi, Hops, ENERGIA e Analisi Fallimenti.
    """
    report_path = os.path.join(output_dir, "REPORT_SUMMARY.txt")

    with open(report_path, "w", encoding="utf-8") as f:

        # Helper per scrivere su entrambi (schermo e file)
        def log(msg=""):
            print(msg)
            f.write(str(msg) + "\n")
            f.flush()

        log("\n" + "#" * 80)
        log("REPORT NUMERICO COMPLETO")
        log("#" * 80)

        def print_table(df, metric_name, x_col, y_col):
            if df is None or df.empty: return
            if y_col not in df.columns: return

            log(f"\n>>> {metric_name} (vs {x_col})")

            # Calcola la media aggregando i seed
            agg = df.groupby(['Algorithm', x_col])[y_col].mean().reset_index()
            agg[y_col] = agg[y_col].round(4)

            try:
                tbl_str = agg.pivot(index=x_col, columns='Algorithm', values=y_col).to_string()
                log(tbl_str)
            except Exception as e:
                log(f"[Errore tabella]: {e}")
            log("-" * 40)

        # --- 1. SUCCESS RATE ---
        if not df_sr_seed.empty:
            df_sr_p = df_sr_seed.copy()
            df_sr_p['Success Rate'] = df_sr_p['Success Rate'] * 100

            # 1.1 vs Arrival Rate
            print_table(df_sr_p, "Success Rate (%)", "Arrival Rate", "Success Rate")

            # 1.2 vs Energy Budget (Solo scenario Stress AT=10)
            target_at = 10.0
            if 'Arrival Rate' in df_sr_p.columns:
                df_sr_budget = df_sr_p[np.isclose(df_sr_p['Arrival Rate'], target_at)]
                if not df_sr_budget.empty:
                    log(f"\n(Nota: Tabella Budget filtrata per Arrival Rate = {target_at})")
                    print_table(df_sr_budget, "Success Rate (%)", "energy_budget", "Success Rate")

        # --- 2. Metriche Temporali e Hops ---
        print_table(df_resp_seed, "Response Time Avg (s)", "Arrival Rate", "Avg")
        print_table(df_resp_seed, "Response Time P95 (s)", "Arrival Rate", "P95")
        print_table(df_rout_seed, "Routing Time Avg (s)", "Arrival Rate", "Avg")
        print_table(df_hops_seed, "AVG Hops (Routing Decision)", "Arrival Rate", "Routing_Avg")
        print_table(df_hops_seed, "AVG Hops (Executed Path)", "Arrival Rate", "Exec_Avg")

        # --- 3. ENERGIA e Load Balancing  ---
        print_table(df_en_task_seed, "Energy per Task (J)", "Arrival Rate", "Energy per Task")
        print_table(df_lb_seed, "Load Balance Degree (Ratio)", "Arrival Rate", "Load Balancing Degree")

        # --- 4. Failure Breakdown ---
        log("\n>>> Fallimenti (Per Arrival Rate = 10)")
        try:
            if df_results is not None and not df_results.empty:
                target_at = 10.0
                if 'Arrival Rate' in df_results.columns:
                    df_failed_10 = df_results[
                        (df_results['Status'] != 'Completed') &
                        (np.isclose(df_results['Arrival Rate'], target_at))
                        ].copy()

                    if not df_failed_10.empty:
                        df_failed_10['Rejection Reason'] = df_failed_10['Rejection Reason'].str.strip()
                        total_fails = df_failed_10.groupby('Algorithm').size()
                        reasons_counts = df_failed_10.groupby(['Algorithm', 'Rejection Reason']).size().unstack(
                            fill_value=0)

                        # Percentuali
                        reasons_perc = reasons_counts.div(total_fails, axis=0) * 100

                        log("Distribuzione cause di fallimento (% sul totale falliti a 10 req/s):")
                        log(reasons_perc.round(2).to_string())


                    else:
                        log("  [Info] Nessun fallimento registrato a Arrival Rate = 10.")
        except Exception as e:
            log(f"  [Errore report fallimenti]: {e}")

        log("\n" + "#" * 80 + "\n")

    print(f"  📄 Report salvato in: {report_path}")
'''def generate_full_report(df_results, df_sr_seed, df_resp_seed, df_rout_seed, df_hops_seed, df_en_task_seed, df_lb_seed):
    """
    Genera un report testuale DETTAGLIATO con i numeri esatti per OGNI metrica.
    """
    print("\n" + "#" * 80 + "\nREPORT NUMERICO COMPLETO\n" + "#" * 80)

    def print_table(df, metric_name, x_col, y_col):
        if df is None or df.empty:
            return

        # Verifica che le colonne esistano
        if y_col not in df.columns:
            return

        print(f"\n>>> {metric_name} (vs {x_col})")

        # Aggrega sui seed (Media) per la visualizzazione tabellare
        # Usiamo 'Algorithm' perché abbiamo rinominato nel main
        agg = df.groupby(['Algorithm', x_col])[y_col].mean().reset_index()
        agg[y_col] = agg[y_col].round(4)

        try:
            print(agg.pivot(index=x_col, columns='Algorithm', values=y_col).to_string())
        except Exception as e:
            print(f"[Errore formattazione tabella]: {e}")
        print("-" * 40)

    # --- 1. SUCCESS RATE ---
    if not df_sr_seed.empty:
        df_sr_p = df_sr_seed.copy()
        df_sr_p['Success Rate'] = df_sr_p['Success Rate'] * 100
        print_table(df_sr_p, "Success Rate (%)", "Arrival Rate", "Success Rate")

    # --- 2. RESPONSE TIME ---
    print_table(df_resp_seed, "Response Time Avg (s)", "Arrival Rate", "Avg")
    print_table(df_resp_seed, "Response Time P95 (s)", "Arrival Rate", "P95")

    # --- 3. ROUTING TIME (Mancava) ---
    print_table(df_rout_seed, "Routing Time Avg (s)", "Arrival Rate", "Avg")

    # --- 4. HOPS (Mancava) ---
    print_table(df_hops_seed, "AVG Hops (Routing Decision)", "Arrival Rate", "Routing_Avg")
    print_table(df_hops_seed, "AVG Hops (Executed Path)", "Arrival Rate", "Exec_Avg")

    # --- 5. ENERGY ---
    print_table(df_en_task_seed, "Energy per Task (J)", "Arrival Rate", "Energy per Task")

    # --- 6. LOAD BALANCING ---
    print_table(df_lb_seed, "Dynamic Load Balancing Degree (Ratio)", "Arrival Rate", "Load Balancing Degree")

    # --- 7. FAILURE REASONS BREAKDOWN (Mancava - usa df_results) ---
    print("\n>>> Breakdown Fallimenti (Focus su Arrival Rate = 10)")
    try:
        if df_results is not None and not df_results.empty:
            # Filtra per Arrival Rate = 10
            # Nota: usa np.isclose se 'Arrival Rate' è float
            target_at = 10.0
            if 'Arrival Rate' in df_results.columns:
                df_failed_10 = df_results[
                    (df_results['Status'] != 'Completed') &
                    (np.isclose(df_results['Arrival Rate'], target_at))
                    ].copy()

                if not df_failed_10.empty:
                    df_failed_10['Rejection Reason'] = df_failed_10['Rejection Reason'].str.strip()

                    # Conta totale fallimenti per algoritmo
                    total_fails = df_failed_10.groupby('Algorithm').size()

                    # Conta fallimenti per motivo
                    reasons_counts = df_failed_10.groupby(['Algorithm', 'Rejection Reason']).size().unstack(
                        fill_value=0)

                    # Calcola percentuali
                    reasons_perc = reasons_counts.div(total_fails, axis=0) * 100

                    print("Distribuzione cause di fallimento (% sul totale falliti a 10 req/s):")
                    print(reasons_perc.round(2).to_string())
                else:
                    print("  [Info] Nessun fallimento registrato a Arrival Rate = 10.")
    except Exception as e:
        print(f"  [Errore report fallimenti]: {e}")

    print("\n" + "#" * 80 + "\n")
'''

# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------
def main():
    # 1. Caricamento
    dataframes = {}
    for key, path in FILE_PATHS.items():
        if os.path.exists(path):
            dataframes[key] = pd.read_csv(path, low_memory=False)
        else:
            print(f"File non trovato: {path}")
            return

    df_results = dataframes["results"]
    df_energy = dataframes["energy"]

    # --- RINOMINA COLONNA 'solver' -> 'Algorithm' ---
    # Questo è il passaggio chiave per far funzionare tutto il resto
    if 'solver' in df_results.columns:
        df_results.rename(columns={'solver': 'Algorithm'}, inplace=True)
    if 'solver' in df_energy.columns:
        df_energy.rename(columns={'solver': 'Algorithm'}, inplace=True)

    # 2. Disambiguazione e Pulizia
    df_results = disambiguate_algorithms(df_results)
    df_energy = disambiguate_algorithms(df_energy)

    rename_map = {'ILP': 'ILP Hierarchical'}
    if 'Algorithm' in df_results.columns:
        df_results['Algorithm'] = df_results['Algorithm'].replace(rename_map)
    if 'Algorithm' in df_energy.columns:
        df_energy['Algorithm'] = df_energy['Algorithm'].replace(rename_map)

    # 3. Filtri
    df_results = apply_filters(df_results, GLOBAL_FILTERS)
    df_energy = apply_filters(df_energy, GLOBAL_FILTERS)

    # 4. Colori Globali
    all_algos = sorted(df_results['Algorithm'].unique())
    global GLOBAL_COLOR_MAP
    GLOBAL_COLOR_MAP = dict(zip(all_algos, sns.color_palette("colorblind", len(all_algos))))

    # 5. Preprocessing (Restituisce Dati PER SEED)
    (df_res_proc, df_sr, df_en_task, df_en_sen, df_resp, df_rout, df_hops) = preprocess_data(df_results, df_energy)
    df_lb = preprocess_data_load_balancing(df_energy)

    # 6. Plot
    if 'Arrival Rate' in df_res_proc.columns:
        plot_1_success_rate(df_sr)
        plot_2_energy(df_en_task, df_en_sen, df_sr)
        plot_3_response_time(df_resp)
        plot_4_routing_time(df_rout)
        plot_5_hops(df_hops)
        plot_8_load_balancing_degree(df_lb)

    # 7. Special Plots
    plot_6_1_failure_reason(df_res_proc, [10.0, 8.0])

    ALGORITHMS_SHOW = ['DTS-base', 'DTS-APopt', 'OrbitAware', 'ILP Hierarchical (Time)', 'ILP Hierarchical (Energy)']

    plot_7_load_balancing_heatmaps(df_energy, GLOBAL_FILTERS, OUTPUT_DIR, ALGORITHMS_SHOW)
    plot_7_3_aggregated_rank_heatmap(df_energy, GLOBAL_FILTERS, OUTPUT_DIR, ALGORITHMS_SHOW)
    plot_load_balancing_heatmap_V2(df_energy, GLOBAL_FILTERS, OUTPUT_DIR)

    # 8. Report
    generate_full_report(df_res_proc, df_sr, df_resp, df_rout, df_hops, df_en_task, df_lb, OUTPUT_DIR)

    print("\n--- Completato. ---")


if __name__ == "__main__":
    main()