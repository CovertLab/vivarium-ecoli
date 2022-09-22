import os
import re
from functools import reduce
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd
from ecoli.library.cell_wall.column_sampler import geom_sampler, sample_lattice

from ecoli.library.create_timeline import add_computed_value, create_timeline_from_df
from ecoli.processes.antibiotics.cell_wall import CellWall
from vivarium.core.composition import add_timeline, simulate_composite
from vivarium.processes.divide_condition import DivideCondition
from vivarium.library.units import units, remove_units
from vivarium.core.serialize import deserialize_value
from vivarium.plots.simulation_output import plot_variables
from vivarium.plots.topology import plot_topology

from ecoli.processes.antibiotics.pbp_binding import PBPBinding

DATA = "data/cell_wall/cell_wall_test_rig_17_09_2022_00_41_51.csv"  # "data/cell_wall/cell_wall_test_rig_31_07_2022_00_26_44.csv"


def parse_unit_string(unit_str):
    parse_result = re.search(r"!units\[(?P<value>\d+[.]\d+) (?P<units>\w+)\]", unit_str)
    return float(parse_result["value"]) * units.parse_expression(parse_result["units"])


def create_composite(timeline_data, antibiotics=True, divide_time=None):
    # Create timeline process from saved simulation
    timeline = create_timeline_from_df(
        timeline_data,
        {
            "CPD-12261[p]": ("bulk", "CPD-12261[p]"),
            "CPLX0-7717[m]": ("bulk", "CPLX0-7717[m]"),
            "CPLX0-3951[i]": ("bulk", "CPLX0-3951[i]"),
            "Volume": ("cell_global", "volume"),
        },
    )

    # Add cell volume, and account for too much murein
    timeline = add_computed_value(
        timeline,
        lambda t, value: {
            ("cell_global", "volume"): parse_unit_string(
                value[("cell_global", "volume")]
            ),
            ("concentrations", "beta_lactam"): (
                9.16 * units.micromolar
                if antibiotics and t > 0
                else 0 * units.micromolar
            ),
            ("bulk", "CPD-12261[p]"): int(value[("bulk", "CPD-12261[p]")]),
            ("should_divide",): divide_time is not None and t >= divide_time,
        },
    )

    # Add cell wall process, pbp binding process, and timeline process into composite
    processes = {
        "cell_wall": CellWall({}),
        "pbp_binding": PBPBinding({}),
        "divider": DivideCondition({"threshold": True}),
    }
    topology = {
        "cell_wall": {
            "shape": ("cell_global",),
            "bulk_murein": ("bulk",),
            "murein_state": ("murein_state",),
            "PBP": ("bulk",),
            "wall_state": ("wall_state",),
            "pbp_state": ("pbp_state",),
            "listeners": ("listeners",),
        },
        "pbp_binding": {
            "total_murein": ("bulk",),
            "murein_state": ("murein_state",),
            "concentrations": ("concentrations",),
            "bulk": ("bulk",),
            "pbp_state": ("pbp_state",),
        },
        "divider": {
            "variable": ("should_divide",),
            "divide": (".",)
        }
    }

    add_timeline(processes, topology, timeline)

    return {"processes": processes, "topology": topology}


def output_data(data, filepath="out/processes/cell_wall/test_cell_wall.png"):
    variables = [
        ("concentrations", "beta_lactam"),
        ("wall_state", "cracked"),
        ("bulk", "CPD-12261[p]"),
        ("bulk", "CPLX0-7717[m]"),
        ("bulk", "CPLX0-3951[i]"),
        ("murein_state", "incorporated_murein"),
        ("murein_state", "unincorporated_murein"),
        ("murein_state", "shadow_murein"),
        ("wall_state", "extension_factor"),
        ("pbp_state", "active_fraction_PBP1A"),
        ("pbp_state", "active_fraction_PBP1B"),
        ("listeners", "porosity"),
    ]

    fig, axs = plt.subplots(len(variables), 1)
    T = sorted(data.keys())
    for i, path in enumerate(variables):
        # Get the data at the specified path for each timepoint t
        # (reduce expression follows the path in data[t])
        var_data = [
            remove_units(deserialize_value(reduce(lambda d, p: d[p], path, data[t])))
            for t in T
        ]

        axs[i].plot(T, var_data)
        axs[i].set_title(path[-1])

    fig.set_size_inches(6, 1.5 * len(variables))
    fig.tight_layout()
    fig.savefig(filepath)


def test_cell_wall():
    timeline_data = pd.read_csv(DATA, skipinitialspace=True)

    composite = create_composite(timeline_data, antibiotics=True, divide_time=None)
    plot_topology(
        composite, out_dir="out/processes/cell_wall/", filename="test_rig_topology.png"
    )

    # Create initial state
    initial_murein = int(timeline_data.iloc[0]["CPD-12261[p]"])
    initial_PBP1A = int(timeline_data.iloc[0]["CPLX0-7717[m]"])
    initial_PBP1B = int(timeline_data.iloc[0]["CPLX0-3951[i]"])
    initial_volume = parse_unit_string(timeline_data.iloc[0]["Volume"])

    initial_state = {
        "bulk": {
            "CPD-12261[p]": initial_murein,
            "CPLX0-7717[m]": initial_PBP1A,
            "CPLX0-3951[i]": initial_PBP1B,
        },
        "murein_state": {
            "incorporated_murein": 0,
            "unincorporated_murein": initial_murein * 4,
            "shadow_murein": 0,
        },
        "wall_state": {},
        "cell_global": {"volume": initial_volume},
        "should_divide": False
    }

    settings = {
        "return_raw_data": True,
        "total_time": 2300,
        "initial_state": initial_state,
        "emitter": "timeseries",
    }

    data = simulate_composite(composite, settings)
    output_data(data)


def main():
    test_cell_wall()


if __name__ == "__main__":
    main()
