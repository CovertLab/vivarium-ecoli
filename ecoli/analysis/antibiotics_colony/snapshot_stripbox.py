import os
import argparse
from typing import Union, Tuple, List

import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

from ecoli.analysis.antibiotics_colony.plot_utils import (
    retrieve_data,
    get_config_names
)

Color = Union[str, Tuple[float, float, float, float]]


def plot_boxstrip(
    data: pd.DataFrame,
    out: str
) -> None:
    '''Plot data as a collection of strip plots with overlaid boxplots.

    Args:
        data: DataFrame where each column is a variable to plot and each row
            is an agent. Data from all replicates is concatenated into this
            single DataFrame and labelled with a different hex color in
            the "Color" column. The DataFrame also has a "Condition" column
            that labels each experimental condition with a unique string.
        out: Prefix for ouput filenames in out/analysis/antibiotics_colony/
    '''
    colors = data["Color"].unique()
    palette = {color: color for color in colors}
    for column in data.columns:
        if column not in ["Color", "Condition", "Time", "Division", "Death", "Agent ID"]:
            g = sns.catplot(
                data=data, kind="box",
                x="Condition", y=column, col="Time",
                boxprops={'facecolor':'None'}, showfliers=False,
                aspect=0.5, legend=False)
            g.map_dataframe(sns.stripplot, x="Condition", y=column,
                hue="Color", palette=palette, alpha=0.5, size=3)
            plt.tight_layout()
            g.savefig('out/analysis/antibiotics_colony/' + 
                f'{out}_{column.replace("/", "_")}.png')
            plt.close(g)


def make_stripbox_plots(
    baseline_configs: List[str],
    exp_configs: List[str],
    baseline_colors: List[str],
    exp_colors: List[str],
    sampling_rate: int,
    cpus: int = 8,
    out: str = 'stripbox'
):
    """Helper function to retrieve and plot timeseries data.
    
    Args:
        baseline_configs: List of JSON filenames to configure
            baseline data retrieval (each is a replicate).
        exp_configs: List of JSON filenames to configure
            experimental data retrieval (each is a replicate).
        baseline_colors: Hex colors for baseline replicates.
        exp_colors: Hex colors for experimental replicates.
        div_and_death: Mark death and division on plots (X and +)
        cpus: Number of CPU cores to use.
        out: Prefix for output plot filenames
    """
    data = retrieve_data(
        configs=baseline_configs,
        colors=baseline_colors, 
        sampling_rate=sampling_rate,
        cpus=cpus)
    exp_data = retrieve_data(
        configs=exp_configs,
        colors=exp_colors, 
        sampling_rate=sampling_rate,
        cpus=cpus)
    data = pd.concat([data, exp_data], ignore_index=True)
    os.makedirs('out/analysis/antibiotics_colony/', exist_ok=True)
    plot_boxstrip(data, out)


def main():
    sns.set_style("white")
    amp_configs = get_config_names('ampicillin')
    tet_configs = get_config_names('tetracycline')
    glc_tet_configs = get_config_names('glucose_tet')
    glc_amp_configs = get_config_names('glucose_amp')
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--tetracycline', '-t', type=bool, default=True,
        help="""Compare tetracycline sims with glucose sims."""
    )
    parser.add_argument(
        '--ampicillin', '-a', type=bool, default=True,
        help="""Compare ampicillin sims with glucose sims."""
    )
    parser.add_argument(
        '--division_and_death', '-d', type=bool, default=True,
        help="""Mark values for agents right before cell division with
            a "+" sign. Mark values for agents right before cell death
            with a "x" sign."""
    )
    parser.add_argument(
        '--cpus', '-p', type=int, default=1,
        help="""Number of CPU cores to use."""
    )
    args = parser.parse_args()
    # Shades of grey for baseline distributions (up to 3 replicates)
    baseline_colors = ('#333333', '#777777', '#BBBBBB')
    # Shades of blue-green for experimental distributions (up to 3 replicates)
    colors = ('#5F9EA0', '#088F8F', '#008080')
    if args.tetracycline:
        make_stripbox_plots(
            baseline_configs=glc_tet_configs,
            exp_configs=tet_configs,
            baseline_colors=baseline_colors,
            exp_colors=colors,
            div_and_death=args.division_and_death,
            cpus=args.cpus,
            out='tetracycline_sb')
    if args.ampicillin:
        make_stripbox_plots(
            baseline_configs=glc_amp_configs,
            exp_configs=amp_configs,
            baseline_colors=baseline_colors,
            exp_colors=colors,
            div_and_death=args.division_and_death,
            cpus=args.cpus,
            out='ampicillin_sb')


if __name__ == "__main__":
    main()
