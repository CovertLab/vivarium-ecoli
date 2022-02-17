import os
from collections import Counter
import numpy as np
from scipy.sparse import coo_matrix
import matplotlib.pyplot as plt

from ecoli.experiments.ecoli_master_sim import EcoliSim, CONFIG_DIR_PATH
from ecoli.plots.blame_utils import get_bulk_processes, extract_bulk, idx_array_from


def validate_data(data):
    if 'log_update' not in data[0.0].keys():
        raise ValueError(
            "Missing log_update store in data; did you run simulation without logged updates?")


def preprocess_data(data, bulk_processes, molecules):
    """
    Prepares raw data for blame-timeseries plot.
    Returns data in the form time, process, values_array
    where time is a numpy array of times, process is a list of 
    process names, and values_array is a numpy array of the form
    (molecule x time x process).
    """

    processes = list(bulk_processes.keys())
    x = np.array(list(data.keys()))

    values_array = np.zeros([len(molecules), len(x), len(processes)])
    for j, timepoint in enumerate(x):
        for k, process in enumerate(processes):
            for i, molecule in enumerate(molecules):
                for path in bulk_processes[process]:
                    values_array[i, j, k] += data[timepoint]['log_update'][process].get(
                        path, {}).get(molecule, 0)

    return x, processes, values_array


def signed_stacked_bar(ax, x, y, bar_labels):
    """
    ax: Axes object
    x: x values (1d array)
    y: y-values (len(x) columns by # stacked bars rows)

    Creates a stacked bar chart in the specified Axes, where
    y's with negative values represent bars below y=0, and
    y's with positive values represent bars above y=0.
    """
    # Need to keep track of separate totals for positive, negative
    # entries at each time step, so that positive entries get stacked above 0,
    # and negative entries get stacked below.
    total_pos = np.zeros_like(x)
    total_neg = np.zeros_like(x)
    
    for series in range(y.shape[1]):
        data = y[:, series]
        ax.bar(x, data,
               bottom=np.where(data > 0, total_pos, total_neg),
               label=bar_labels[series])
        total_pos += np.clip(data, 0, None)
        total_neg += np.clip(data, None, 0)

    # Plot net change
    ax.plot(x, total_pos + total_neg,
            color="k", label="net change")


def blame_timeseries(data,
                     topology,
                     molecules,
                     filename=None,
                     yscale='linear'):
    """
    Generates timeseries blame plots for the selected molecules, saving
    to the specified output file. Timeseries blame plots show the change in molecule
    counts due to each process at each timestep. For convenience, a small plot of count
    is included to the side.

    Example usage:
    ```
    sim = EcoliSim.from_file()
    sim.run()
    data = sim.query()
    blame_timeseries(data, sim.topology,
                     ['WATER[c]', 'APORNAP-CPLX[c]', 'TRP[c]'],
                     'out/ecoli_master/test_blame_timeseries.png',
                     yscale="linear")
    ```

    Returns:
        - axes (Axes object)
        - fig (Figure object)
    """

    validate_data(data)

    # Collect data into one dictionary
    # of the form: {process : {molecule : timeseries}}
    bulk_processes = get_bulk_processes(topology)
    time, processes, values_array = preprocess_data(data, bulk_processes, molecules)     

    # Twp subplots per molecule (count, change)
    max_t = time.max()
    fig, axs = plt.subplots(len(molecules), 2,
                            figsize=(10 + np.sqrt(max_t), 3*len(molecules)),
                            gridspec_kw={'width_ratios': [1, 10 + np.sqrt(max_t)]})
    for i, molecule in enumerate(molecules):
        # Plot molecule count over time
        # molecule_data = data['bulk'][molecule]
        molecule_data = np.array([timepoint['bulk'].get(molecule, 0) for timepoint in data.values()])
        axs[i, 0].set_title(f"Count of {molecule}", pad=20)
        axs[i, 0].set_ylabel("# molecules")
        axs[i, 0].set_xlabel("Time (s)")
        axs[i, 0].set_xticks(time)
        axs[i, 0].plot(time, molecule_data)

        # Plot change due to each process
        axs[i, 1].set_title(f"Change in {molecule}", pad=20)
        axs[i, 1].set_ylabel("# molecules")
        axs[i, 1].set_xlabel("Time (s)")
        axs[i, 1].set_xticks(time[1:])
        axs[i, 1].axhline(y=0, color="k", linestyle="--", alpha=0.5)

        y = values_array[i, 1:len(time), :]
        signed_stacked_bar(axs[i,1], time[1:], y, processes)
        axs[i, 1].set_yscale(yscale)

    axs[0, 1].legend(bbox_to_anchor=(1.04, 0.5),
                     loc="center left", borderaxespad=0)

    # Sizing and spacing
    # fig.set_size_inches(4 + np.sqrt(max_t),  # include space for legend(s)
    #                     3 * len(molecules))  # height prop. to number of plots
    fig.tight_layout(pad=2.0)

    # Save plot to file
    if filename:
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        plt.savefig(filename)

    # Return axis and figure
    return plt.gca(), plt.gcf()


def test_blame_timeseries():
    # TODO:
    # - add back processes
    # - get working with unique molecules (separate argument for unique)
    # - serializers

    from vivarium.core.emitter import (
        data_from_database, get_local_client, timeseries_from_data)

    EXPERIMENT_ID = None  # "d811d69e-0cf6-11ec-a1ab-00155df92294"

    if EXPERIMENT_ID:
        data, conf = data_from_database(EXPERIMENT_ID,
                                        get_local_client("localhost", "27017", "simulations"))
        data = timeseries_from_data(data)
        topo = conf['topology']

    else:
        sim = EcoliSim.from_file()
        # CONFIG_DIR_PATH + "/test_configs/test_blame.json")
        # sim.emitter = "database"
        sim.partition = True
        sim.raw_output = True
        sim.log_updates = True
        sim.emit_topology = False
        sim.emit_processes = False
        sim.total_time = 4
        # sim.exclude_processes = ["ecoli-two-component-system",
        #                          "ecoli-chromosome-structure",]
        #                          #"ecoli-polypeptide-elongation"]
        sim.run()
        data = sim.query()
        topo = sim.topology

    molecules = [
        "EG10841-MONOMER",
        "EG10321-MONOMER",
        "EG11545-MONOMER",
        "EG11967-MONOMER",
        "FLAGELLAR-MOTOR-COMPLEX",
        "G361-MONOMER",
        "CPLX0-7451",
        "CPLX0-7452"  # Final flagella molecule
    ]

    blame_timeseries(data, topo,
                     ['WATER[c]', 'APORNAP-CPLX[c]', 'TRP[c]'] + molecules,
                     'out/ecoli_master/test_blame_timeseries.png',
                     yscale="linear")


if __name__ == "__main__":
    test_blame_timeseries()
