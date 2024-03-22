import argparse
import ast
import concurrent.futures
import os
import warnings
import json
import matplotlib
import numpy as np
import pandas as pd
import seaborn as sns
from tqdm import tqdm

from ecoli.analysis.antibiotics_colony.plot_utils import prettify_axis

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.axes_grid1 import make_axes_locatable
from mpl_toolkits.axes_grid1.anchored_artists import AnchoredSizeBar
from vivarium.core.emitter import DatabaseEmitter
from vivarium.library.dict_utils import get_value_from_path
from vivarium.library.topology import convert_path_style

from ecoli.analysis.db import access_counts_old, deserialize_and_remove_units
from ecoli.plots.snapshots import format_snapshot_data, get_tag_ranges, plot_tags

from ecoli.analysis.antibiotics_colony import COUNTS_PER_FL_TO_NANOMOLAR, PATHS_TO_LOAD


PERIPLASMIC_VOLUME_FRACTION = 0.2
PERIPLASMIC_VARS = ["OmpF monomer", "TolC monomer", "AmpC monomer"]


def make_snapshot_and_hist_plot(
    timepoint_data, metadata, bounds, molecule, title=None, tag_hsv=[0.6, 1, 1]
):
    """Generates a figure with a snapshot plot tagging the specified molecule,
    and a smoothed density plot (using histogram) of the distribution of counts for that molecule
    at that time.
    Args:
        timepoint_data: data from one timepoint, in the form {time : {...data...}}
        bounds: physical bounds for the snapshot plot
        molecule: molecule to tag in the snapshot plot / histogram plot.
    Returns:
        fig, axes"""

    # time = list(timepoint_data.keys())
    time = timepoint_data["Time"].unique()
    assert len(time) == 1, f"Expected only one timepoint, got {time}"
    time = time[0]

    condition = list(metadata.keys())
    assert len(condition) == 1
    condition = condition[0]

    seed = list(metadata[condition].keys())
    assert len(seed) == 1
    seed = seed[0]

    # Convert DataFrame data back to dictionary form for tag plot
    timepoint_data = {
        time: {
            "agents": {
                agent_id: {
                    "boundary": boundary,
                    # Convert from counts to uM
                    molecule: (
                        (
                            molecule_count
                            / (
                                boundary["volume"]
                                * (
                                    PERIPLASMIC_VOLUME_FRACTION
                                    if molecule in PERIPLASMIC_VARS
                                    else 1 - PERIPLASMIC_VOLUME_FRACTION
                                )
                            )
                        )
                        * COUNTS_PER_FL_TO_NANOMOLAR
                        / 10**3  # Convert to uM
                    ),
                }
                for agent_id, boundary, molecule_count in zip(
                    timepoint_data.loc[:, "Agent ID"],
                    timepoint_data.loc[:, "Boundary"],
                    timepoint_data.loc[:, molecule],
                )
            },
            "fields": metadata[condition][seed]["fields"][time],
        }
    }

    # Get snapshot figure from plot_tags
    fig = plot_tags(
        timepoint_data,
        bounds,
        snapshot_times=[time],
        tagged_molecules=[(molecule,)],
        show_timeline=False,
        background_color="white",
        default_font_size=10,
        scale_bar_length=None,  # TODO: scale bar length looks wrong?
        min_color="white",
        tag_colors={(molecule,): tag_hsv},
        convert_to_concs=False,
        xlim=[8, 42],
        ylim=[8, 42],
    )
    tag_axes = fig.get_axes()
    snapshot_ax = tag_axes[0]

    # Prettify axis labels
    snapshot_ax.set(ylabel=None)
    snapshot_ax.set_title(molecule[-1] if title is None else title)

    grid = fig.add_gridspec(
        2, 2, width_ratios=[2, 1], height_ratios=[3, 1], wspace=0, hspace=0
    )

    # Reposition axes, preparing to add hist plot below
    snapshot_ax.set_position(grid[0, 0].get_position(fig))
    snapshot_ax.set_subplotspec(grid[0, 0])

    # Remove colorbar axes (recreating is easier than re-positioning)
    for ax in fig.get_axes():
        if ax != snapshot_ax:
            ax.remove()

    # re-create colorbar
    divider = make_axes_locatable(snapshot_ax)
    cax = divider.append_axes("bottom", size="5%", pad=0.05)

    agents, _ = format_snapshot_data(timepoint_data)
    tag_ranges, _ = get_tag_ranges(
        agents, [(molecule,)], [0], False, {(molecule,): tag_hsv}
    )
    min_tag, max_tag = tag_ranges[(molecule,)]
    norm = matplotlib.colors.Normalize(vmin=min_tag, vmax=max_tag)
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list(
        "", ["white", np.array(matplotlib.colors.hsv_to_rgb(tag_hsv))]
    )
    mappable = matplotlib.cm.ScalarMappable(norm, cmap)

    cbar = fig.colorbar(
        mappable,
        cax=cax,
        orientation="horizontal",
        ticks=[min_tag, max_tag],
    )
    format_tick = (
        lambda tick: f"{tick:.0f}" if tick == 0 or tick > 100 else f"{tick:.1f}"
    )

    cbar.ax.set_xticklabels(
        [f"{format_tick(min_tag)} μM", f"{format_tick(max_tag)} μM"], fontsize=8
    )

    # Add histogram plot
    hist_ax = fig.add_subplot(grid[1, 0])

    # Get distribution of concentration across agents
    hist_data = {
        molecule[-1]: [
            get_value_from_path(agent_data, (molecule,))
            for agent_data in timepoint_data[time]["agents"].values()
        ]
    }
    hist_data = pd.DataFrame(hist_data)

    # Plot histogram
    sns.histplot(
        data=hist_data,
        x=molecule[-1],
        ax=hist_ax,
        color=matplotlib.colors.hsv_to_rgb(tag_hsv),
    )

    # Aesthetics
    hist, bins = np.histogram(hist_data, bins="auto")
    hist_ax.set_xlabel(None)
    hist_ax.set_ylabel("Cells", fontsize=9, labelpad=-5)
    hist_ax.set(
        xticks=[bins[0], bins[-1]],
        xticklabels=[f"{format_tick(bins[0])} μM", f"{format_tick(bins[-1])} μM"],
        xlim=[bins[0], bins[-1]],
        yticks=[0, max(hist)],
        ylim=[0, max(hist)],
    )
    prettify_axis(
        hist_ax,
        label_fontsize=9,
        ticklabel_fontsize=8,
        tick_format_x="{:.1f} μM",
        tick_format_y="{:.0f}",
    )
    hist_ax.set_xticks(
        [min_tag, max_tag],
        labels=[f"{format_tick(min_tag)} μM", f"{format_tick(max_tag)} μM"],
    )
    # hist_ax.set_box_aspect(1)

    return fig, fig.get_axes()


def get_data(experiment_id, time, molecules, host, port, cpus, verbose):

    # Prepare molecule paths for access_counts()
    monomers = [m[-1] for m in molecules if m[-2] == "monomer"]
    mrnas = [m[-1] for m in molecules if m[-2] == "mrna"]
    inner_paths = [
        path for path in molecules if path[-1] not in mrnas and path[-1] not in monomers
    ]
    outer_paths = [("data", "dimensions")]

    if verbose:
        print(f"Accessing data for experiment {experiment_id}...")

    # Query database
    data = access_counts_old(
        experiment_id=experiment_id,
        monomer_names=monomers,
        mrna_names=mrnas,
        inner_paths=inner_paths,
        outer_paths=outer_paths,
        host=host,
        port=port,
        start_time=time,
        end_time=time,
        cpus=cpus,
    )

    with concurrent.futures.ProcessPoolExecutor(cpus) as executor:
        # Prepare to deserialize data
        data_deserialized = executor.map(deserialize_and_remove_units, data.values())

        # If verbose, add a progress bar
        if verbose:
            data_deserialized = tqdm(
                data_deserialized, desc="Deserializing data", total=len(data)
            )

        # Do the actual deserializing (lazy computation)
        data_deserialized = list(data_deserialized)

    # prep data, physical bounds for returning
    data = dict(zip(data.keys(), data_deserialized))
    bounds = data[time]["dimensions"]["bounds"]

    return data, bounds


def main():
    parser = argparse.ArgumentParser(
        "Generate snapshot and histogram figures for specified molecules."
    )

    parser.add_argument(
        "experiment_id",
        help="ID of the experiment for which to make the figure(s).",
    )
    parser.add_argument(
        "--molecule_paths",
        "-m",
        nargs="+",
        required=True,
        help="Paths (in A>B>C form) of the molecule(s) for which to generate figure(s). "
        'Can be preceded by an alias for that molecule, e.g. "OmpF=monomer>EG10671-MONOMER".',
    )
    parser.add_argument(
        "--time",
        "-t",
        type=int,
        default=None,
        help="Timepoint which to plot (defaults to last timepoint).",
    )
    parser.add_argument(
        "--outdir",
        "-d",
        default="out/snapshot_hist_plots",
        help="Directory in which to output the generated figures.",
    )
    parser.add_argument("--svg", "-s", action="store_true", help="Save as svg.")
    parser.add_argument("--host", "-o", default="localhost", type=str)
    parser.add_argument("--port", "-p", default=27017, type=int)
    parser.add_argument(
        "--local",
        "-l",
        default=None,
        type=str,
        help="Locally saved dataframe file to run the plots on (if provided). "
        "Setting this option overrides database options (experiment_id, host, port).",
    )
    parser.add_argument("--cpus", "-c", type=int, default=1)
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()

    # Covert molecule path styles, get names
    molecules = []
    molecule_names = []
    for path in args.molecule_paths:
        p = path.split("=")
        if len(p) == 1:  # no alias given
            p = convert_path_style(p[0])
            molecules.append(p)
            molecule_names.append(p[-1])
        else:
            molecules.append(convert_path_style(p[-1]))
            molecule_names.append(p[0])

    if args.local:
        # Load data
        data = pd.read_csv(args.local, dtype={
            'Agent ID': str, 'Seed': str}, index_col=0)
        # Convert string to dictionary
        data['Boundary'] = data['Boundary'].apply(ast.literal_eval)

        # Get only desired columns
        paths_to_columns = {v: k for k, v in PATHS_TO_LOAD.items() if v in molecules}
        for missing in [p for p in molecules if p not in paths_to_columns]:
            warnings.warn(f"Path {missing} is missing from locally saved dataframe.")
        keep_columns = [
            "Agent ID",
            "Dry mass",
            "Growth rate",
            "Time",
            "Seed",
            "Condition",
            "Boundary",
            *paths_to_columns.values(),
        ]
        data = data[keep_columns]

        # Load metadata
        if args.verbose:
            print(
                "Loading metadata; filename must have the form <data_filename>_metadata.<data_ext>"
            )

        filename, ext = os.path.splitext(args.local)
        with open(f"{filename}_metadata.json", "r") as f:
            metadata = json.load(f)

        # Get environmental bounds
        condition = list(metadata.keys())[0]
        seed = list(metadata[condition].keys())[0]
        bounds = metadata[condition][seed]["bounds"]

        # Get max time if no time specified
        time = args.time
        if time is None:
            if args.verbose:
                print(
                    "No timepoint given, trying to infer and use final timepoint from data.\n"
                    "If this fails, consider specifying an explicit timepoint."
                )
            time = data["Time"].max()

        # Restrict data to come from only one timepoint
        data = data[data["Time"] == time]
    else:
        # Get max time if no time specified
        time = args.time
        if time is None:
            if args.verbose:
                print(
                    "No timepoint given, trying to infer and use final timepoint from data.\n"
                    "If this fails, consider specifying an explicit timepoint."
                )

            config = {"host": f"{args.host}:{args.port}", "database": "simulations"}
            emitter = DatabaseEmitter(config)
            db = emitter.db

            time = list(
                db.history.aggregate(
                    [
                        {"$match": {"experiment_id": args.experiment_id}},
                        {"$project": {"data.time": 1}},
                        {"$group": {"_id": None, "time": {"$max": "$data.time"}}},
                    ]
                )
            )[0]["time"]

        # Get data from database
        data, bounds = get_data(
            experiment_id=args.experiment_id,
            time=2 * (time // 2),  # only even timesteps have the data necessary
            molecules=molecules,
            host=args.host,
            port=args.port,
            cpus=args.cpus,
            verbose=args.verbose,
        )

        # TODO: Get this in Dataframe format (see plot.py?)

    # Generate one figure per molecule
    plt.rcParams["font.family"] = "Arial"
    plt.rcParams["svg.fonttype"] = "none"
    os.makedirs(args.outdir, exist_ok=True)
    for name, molecule in zip(molecule_names, molecules):
        if args.verbose:
            print(f"Plotting snapshot + histogram for {name}={molecule[-1]}...")

        fig, _ = make_snapshot_and_hist_plot(
            data, metadata, bounds, paths_to_columns[molecule], title=""
        )

        fig.set_size_inches(2.25, 2.9)
        fig.tight_layout()
        fig.savefig(
            os.path.join(
                args.outdir,
                f"snapshot_and_hist_{name}.{'svg' if args.svg else 'png'}",
            )
        )
        plt.close(fig)


if __name__ == "__main__":
    main()
