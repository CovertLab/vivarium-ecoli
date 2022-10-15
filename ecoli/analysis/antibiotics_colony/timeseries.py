from typing import List

import numpy as np
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt


def plot_timeseries(
    data: pd.DataFrame,
    out: str = None,
    axes: List[plt.Axes] = None,
) -> None:
    '''Plot data as a collection of timeseries with IQRs.

    Args:
        data: DataFrame where each column is a variable to plot and each row
            is an agent. Data from all replicates is concatenated into this
            single DataFrame and labelled with a different hex color in
            the "color" column. The DataFrame also has a "Condition" column
            that labels each experimental condition with a unique string.
        out: Prefix for output plot filename. Separate plots will be created
            and saved for each column in data. Do not use with ``axes``.
        axes: If supplied, columns are plotted sequentially on these Axes.
    '''
    metadata_columns = ["Color", "Condition", "Time", "Division", "Death", "Agent ID"]
    colors = data["Color"].unique()
    palette = {color: color for color in colors}
    n_variables = len(data.columns) - len(metadata_columns)
    if not axes:
        _, fresh_axes = plt.subplots(nrows=n_variables, ncols=1, 
            sharex=True, figsize=(2*n_variables, 8))
        axes = np.ravel(fresh_axes)
    ax_idx = 0
    for column in data.columns:
        if column not in metadata_columns:
            curr_ax = axes[ax_idx]
            ax_idx += 1
            g = sns.lineplot(
                data=data, x="Time", y=column, hue="Color", palette=palette,
                errorbar=("pi", 50), legend=False, estimator=np.median, ax=curr_ax)
            if "Death" in data.columns:
                death = data[data["Death"]]
                g = sns.scatterplot(
                    data=death, x="Time", y=column, hue="Color", ax=g,
                    palette=palette, markers=["X"], style="Death", legend=False)
            if "Division" in data.columns:
                divide = data[data["Division"]]
                g = sns.scatterplot(
                    data=divide, x="Time", y=column, hue="Color", ax=g, 
                    palette=palette, markers=["P"], style="Division", legend=False)
            if out:
                fig = g.get_figure()
                plt.tight_layout()
                fig.savefig('out/analysis/antibiotics_colony/' + 
                    f'{out}_{column.replace("/", "_")}.png')
                plt.close(fig)
