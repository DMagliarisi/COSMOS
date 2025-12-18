import globals

# Carica config solo se ti serve localmente (puoi comunque leggere da globals.config)
config = globals.config

def exponential(_=None):
    """
    Distribuzione esponenziale: la chiamata può ricevere un argomento (es. 'Task'),
    che viene ignorato per compatibilità.
    Usa globals.rnd_np per riproducibilità.
    """
    mean = config.get("arrival_time_exponential", 0.5)
    # globals.rnd_np può essere un Generator (default_rng) o RandomState fallback
    try:
        return float(globals.rnd_np.exponential(scale=mean))
    except AttributeError:
        # fallback RandomState
        return float(globals.rnd_np.exponential(mean))


# --- truncated_exponential: due versioni disponibili ---
def truncated_exponential_trunc(mean, lower, upper):
    """
    Versione TRONCATA (come avevi prima) ma usa globals.rnd_np.
    """
    # se globals.rnd_np è Generator:
    try:
        while True:
            value = float(globals.rnd_np.exponential(scale=mean))
            if lower <= value <= upper:
                return value
    except AttributeError:
        # fallback RandomState
        while True:
            value = float(globals.rnd_np.exponential(mean))
            if lower <= value <= upper:
                return value


def truncated_exponential_unbounded(mean, lower=None, upper=None):
    """
    Versione NON TRONCATA per i CPU times (ritorna esponenziale senza limiti).
    Usa 'mean' come parametro di scala.
    Se vuoi un comportamento diverso, cambia qui.
    """
    try:
        return float(globals.rnd_np.exponential(scale=mean))
    except AttributeError:
        return float(globals.rnd_np.exponential(mean))
