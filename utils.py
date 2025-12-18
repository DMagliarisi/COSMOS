import globals

def sendTask(env, task, sender, receiver, algorithm):

    """
    Transfers a task from a sender satellite to a receiver satellite, updating its state and attributes.

    Args:
        task (Task): The task object to be transferred. It contains attributes such as `hop`, `ttl`, 
                     `id`, `current_server`, and `satellite_destination`.
        sender (Satellite): The satellite currently holding the task. It must have a `remove_task` method.
        receiver (Satellite): The satellite to which the task is being sent. It must have an `add_task` method.
        algorithm (Str): Algorithm name.
    Behavior:
        - Increments the `hop` count of the task by 1 to track the number of hops.
        - Decrements the `ttl` (time-to-live) of the task by 1 to reflect its remaining lifespan.
        - Removes the task from the sender using `sender.remove_task(task.id)`.
        - Adds the task to the receiver using `receiver.add_task(task)`.
        - Updates the `current_server` attribute of the task to the receiver.
        - Checks if the receiver is the task's `satellite_destination`. If so, marks the task as arrived by 
          setting `task.arrived` to `True`.
        - Logs the transfer operation in the format: "[task.id] sender.name -> receiver.name".

    Note:
        This function assumes that the `task`, `sender`, and `receiver` objects are properly defined and 
        implement the required attributes and methods.
    """
    if task.ttl > 0:
        # Gestione dell'attesa nell'env
        if receiver.name != 'OBS':
            #bandwidth = sender.bandwidth[receiver] * (1024**2)  # da MB/s a Byte/s
            bandwidth = globals.rnd.randint(globals.config["available_bandwidth"]["min"], globals.config["available_bandwidth"]["max"])
            bandwidth = bandwidth * (1024 ** 2) if bandwidth is not None else 0.0

            trasmission_time = getTransmissionTime(bandwidth, task.weight, sender.latency[receiver])
            print(f"[{task.id}][{algorithm}] {sender.name} -> {receiver.name} | Tramission-time: {trasmission_time}")

            energy_tx = sender.compute_routing_energy(task.weight, bandwidth)
            sender.energy -= energy_tx
            print(
                f"[{task.id}] Energy routing consumed by {sender.name}: {energy_tx:.6f} J (remaining {sender.energy:.2f})")
        else:
            bandwidth = globals.rnd.randint(globals.config["available_bandwidth"]["min"],
                                            globals.config["available_bandwidth"]["max"])
            bandwidth = bandwidth * (1024 ** 2) if bandwidth is not None else 0.0

            trasmission_time = getTransmissionTime(bandwidth, task.weight, 0)
            print(f"[{task.id}][{algorithm}] CONSEGNATO! {sender.name} -> {receiver.name} | Tramission-time: {trasmission_time}")

        task.hop += 1
        task.ttl -= 1   
        
        task.add_algorithm(algorithm)       # Contiamo quale algoritmo abbiamo usato
        
        sender.tasks.remove(task)           # Rimuoviamo il task dal Sender
        yield env.timeout(trasmission_time)
        
        receiver.tasks.append(task)         # Inviamo il task al Receiver
        
        task.current_node = receiver.name   # Modifichiamo le informazioni sul task

        if task.dest_node == receiver.name:
            task.arrived = True
            task.label = 'TASK_ARRIVED'

        #task.hop_History.append(receiver.name)     # Aggiorno la History
        task.visited.add(receiver.name)            # Aggiorno i visitati

    else:
        # Rimuoviamo il task
        print(f"[{task.id}] RIMOZIONE TASK DA {sender.name}, TTL finito")
        task.label = 'TTL_EXPIRED'
        sender.dead_tasks.append(task)
        sender.tasks.remove(task)

def getTransmissionTime(bandwidht, weight, latency):
    return (weight/bandwidht) + latency


def colorize(text: str, color: str) -> str:
    """
    Colora una stringa con i codici ANSI per il terminale.

    Args:
        text (str): La stringa da colorare.
        color (str): Il colore (es: "red", "green", "yellow", "blue", "magenta", "cyan", "white").

    Returns:
        str: La stringa colorata con codici ANSI.
    """
    colors = {
        "black": "\033[30m",
        "red": "\033[91m",
        "green": "\033[92m",
        "yellow": "\033[93m",
        "blue": "\033[94m",
        "magenta": "\033[95m",
        "cyan": "\033[96m",
        "white": "\033[97m",
        "reset": "\033[0m",
        "orange": "\033[33m"
    }

    start = colors.get(color.lower(), "")
    end = colors["reset"] if start else ""
    return f"{start}{text}{end}"
