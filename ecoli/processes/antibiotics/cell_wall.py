"""
TODO: references for parameters
"""

import os

import numpy as np
from matplotlib import pyplot as plt
import mpl_toolkits.mplot3d.axes3d as axes3d
from skimage.transform import resize

from vivarium.core.process import Process
from vivarium.library.units import units
from vivarium.plots.simulation_output import plot_variables

from ecoli.library.cell_wall.hole_detection import detect_holes
from ecoli.library.schema import bulk_schema
from ecoli.processes.registries import topology_registry
from vivarium.core.composition import simulate_process
from ecoli.processes.shape import length_from_volume


# Register default topology for this process, associating it with process name
NAME = "ecoli-cell-wall"
TOPOLOGY = {
    "shape": ("global",),
    "bulk_murein": ("bulk",),
    "murein_state": ("murein_state",),
    "PBP": ("bulk",),
    "wall_state": ("wall_state")
}
topology_registry.register(NAME, TOPOLOGY)


class CellWall(Process):

    name = NAME
    topology = TOPOLOGY
    defaults = {
        # Molecules
        "murein": "CPD-12261[p]",  # two crosslinked peptidoglycan units
        "PBP": {  # penicillin-binding proteins
            "PBP1A": "CPLX0-7717[i]",  # transglycosylase-transpeptidase ~100
            "PBP1B": "CPLX0-3951[i]",  # transglycosylase-transpeptidase ~100
        },
        # Physical parameters
        "critical_radius": 20 * units.nm,
        "cell_radius": 0.25 * units.um,
        # 4.1 in maximally stretched configuration,
        "disaccharide_length": 1.03 * units.nm,
        # divided by 3 because the sacculus can be stretched threefold
        "crossbridge_length": 4.1 * units.nm / 3,
        "peptidoglycan_unit_area": 4
        * units.nm**2,  # replace with precise literature value
        # Simulation parameters
        "seed": 0,
    }

    def __init__(self, parameters=None):
        super().__init__(parameters)

        # Get murein id and keep track of murein from last timestep
        self.murein = self.parameters["murein"]

        self.critical_radius = 20 * units.nm
        self.critical_area = np.pi * self.critical_radius**2
        self.peptidoglycan_unit_area = self.parameters["peptidoglycan_unit_area"]

        # Create pseudorandom number generator
        self.rng = np.random.default_rng(self.parameters["seed"])

    def ports_schema(self):
        schema = {
            "bulk_murein": bulk_schema([self.parameters["murein"]]),
            "murein_state": {
                "free_murein": {"_default": 0, "_updater": "set", "_emit": True},
                "incorporated_murein": {
                    "_default": 0,
                    "_updater": "set",
                    "_emit": True,
                },
            },
            "PBP": bulk_schema(self.parameters["PBP"].values()),
            "shape": {"volume": {"_default": 0 * units.fL, "_emit": True}},
            "wall_state": {
                "lattice": {
                    "_default": np.array([], dtype=int),
                    "_updater": "set",
                    "_emit": True,
                },
                "lattice_rows": {"_default": 0, "_updater": "set", "_emit": True},
                "lattice_cols": {"_default": 0, "_updater": "set", "_emit": True},
                "cracked": {"_default": False, "_updater": "set", "_emit": True},
            },
        }

        return schema

    def initial_state(self, config=None):
        # TODO: better system for initial state - 
        # work this into the state file?
        # Need to incorporate shape process...
        initial_state = {
            "murein_state": {
                "free_murein": 0,
                "incorporated_murein": 12  # config["initial_murein"]
            },
            "shape": {
                "volume": 1 * units.fL
            },
            "wall_state": {
                "lattice": np.ones((1525, 1)),
                "lattice_rows": 1525,
                "lattice_cols": 1,
            }
        }
        return initial_state

    def next_update(self, timestep, states):
        DEBUG = False

        # Unpack states
        volume = states["shape"]["volume"]
        lattice = states["wall_state"]["lattice"]
        lattice_rows = states["wall_state"]["lattice_rows"]
        lattice_cols = states["wall_state"]["lattice_cols"]

        # Translate volume into length
        length = length_from_volume(volume, self.parameters["cell_radius"]*2)

        update = {}

        if DEBUG:
            # states['bulk_murein'][self.murein] = 3000000
            update["shape"] = {"volume": 0.1 * units.fL}
            assert (
                states["bulk_murein"][self.murein]
                == states["murein_state"]["free_murein"]
                + states["murein_state"]["incorporated_murein"]
            )

        # Expand lattice size if necessary, depending on cell size
        print("resizing lattice")
        lattice, rows, columns = self.resize_lattice(
            length, self.parameters["cell_radius"], lattice, lattice_rows, lattice_cols
        )

        # Cell wall construction/destruction
        print("assigning murein")
        lattice, new_free_murein, new_incorporated_murein = self.assign_murein(
            states["murein_state"]["free_murein"],
            states["murein_state"]["incorporated_murein"],
            lattice,
            rows,
            columns,
        )
        print(f"Lattice size: {lattice.shape}")
        print(f"Holes: {lattice.size - lattice.sum()}")

        update["wall_state"] = {
            "lattice": lattice,
            "lattice_rows": rows,
            "lattice_cols": columns,
        }

        update["murein_state"] = {
            "free_murein": new_free_murein,
            "incorporated_murein": new_incorporated_murein,
        }

        # Crack detection (cracking is irreversible)
        print("crack detection")
        if (
            not states["wall_state"]["cracked"]
            and self.get_largest_defect_area(lattice) > self.critical_area
        ):
            update["wall_state"]["cracked"] = True

        assert new_incorporated_murein == lattice.sum()

        return update

    def resize_lattice(
        self, cell_length, cell_radius, lattice, prev_rows=0, prev_cols=0
    ):

        # Calculate new lattice size
        columns = int(
            cell_length.to("nm")
            / (
                self.parameters["crossbridge_length"]
                + self.parameters["disaccharide_length"]
            )
        )
        self.circumference = 2 * np.pi * cell_radius
        rows = int(self.circumference / self.parameters["disaccharide_length"])

        # Fill in new positions with defects initially
        lattice = np.pad(
            lattice,
            ((0, max(0, rows - prev_rows)), (0, max(0, columns - prev_cols))),
            mode="constant",
            constant_values=0,
        )

        assert lattice.shape == (rows, columns)
        return lattice, rows, columns

    def assign_murein(self, free_murein, incorporated_murein, lattice, rows, columns):
        n_incorporated = lattice.sum()
        n_holes = lattice.size - n_incorporated

        # fill holes
        # TODO: Replace random selection with strand extrusion
        #       from a length distribution
        fill_n = min(free_murein, n_holes)

        if fill_n > 0:
            fill_idx = self.rng.choice(
                np.arange(lattice.size), size=fill_n, replace=False
            )
            for idx in fill_idx:
                # Convert 1D index into row, column
                r = idx // columns
                c = idx - (r * columns)

                # fill hole
                lattice[r, c] = 1

        # add holes
        # TODO: Replace random selection with biased selection
        #       based on existing holes/stress map
        new_holes = lattice.sum() - incorporated_murein

        # choose random occupied locations
        if new_holes > 0:
            idx_occupancies = np.array(np.where(lattice))
            idx_new_holes = self.rng.choice(
                idx_occupancies.T, size=new_holes, replace=False
            )

            for hole_r, hole_c in idx_new_holes:
                lattice[hole_r, hole_c] = 0

        total_murein = free_murein + incorporated_murein
        new_incorporated = lattice.sum()
        new_free = total_murein - new_incorporated

        return lattice, new_free, new_incorporated

    def get_largest_defect_area(self, lattice):
        hole_sizes, _ = detect_holes(
            lattice,
            critical_size=int(self.critical_area / self.peptidoglycan_unit_area),
        )
        max_size = hole_sizes.get_max()

        return max_size * self.peptidoglycan_unit_area


def plot_lattice(lattice, on_cylinder=False):
    if not on_cylinder:
        fig, ax = plt.subplots()
        mappable = ax.imshow(lattice, interpolation="nearest")
        fig.colorbar(mappable, ax=ax)
    else:
        print("Downscaling lattice...")
        lattice = resize(
            lattice,
            (lattice.shape[0] // 10, lattice.shape[1] // 10),
            preserve_range=True,
            anti_aliasing=True,
        )
        lattice = lattice.T
        print("Done.\nDrawing on cylinder...")

        h, w = lattice.shape
        theta, z = np.linspace(0, 2 * np.pi, w), np.linspace(0, 1, h)
        THETA, Z = np.meshgrid(theta, z)
        X = np.cos(THETA)
        Y = np.sin(THETA)
        fig = plt.figure()
        ax = fig.add_subplot(1, 1, 1, projection="3d")
        mappable = plt.cm.ScalarMappable(cmap=plt.cm.inferno)
        mappable.set_clim(0, 1)
        mappable.set_array(lattice)
        plot = ax.plot_surface(
            X,
            Y,
            Z,
            rstride=1,
            cstride=1,
            facecolors=mappable.cmap(lattice),
            linewidth=0,
            antialiased=False,
            alpha=0.75,
        )
        fig.colorbar(mappable)

    return fig, ax


def main():
    from vivarium.core.composer import Composite
    from vivarium.processes.timeline import TimelineProcess

    # Stub for rest of cell (increasing murein)
    cell_stub = TimelineProcess(
        {
            "time_step": 2.0,
            "timeline": [
                (time, {("bulk_murein", "CPD-12261[p]"): int(3e6 + 1000 * time)})
                for time in range(0, 10, 2)
            ],
        }
    )

    # Cell wall process
    params = {}
    cell_wall = CellWall(params)

    settings = {
        "total_time": 10,
        "initial_state": {
            "bulk_murein": {"CPD-12261[p]": int(3e6)},
            "volume": 1 * units.fL,
            "murein_state": {"free_murein": 0, "incorporated_murein": 3000000},
            "wall_state": {
                "lattice_rows": 1525,
                "lattice_cols": 834,
                "lattice": np.ones((1525, 834), dtype=int),
            },
        },
    }

    # sim = Engine(
    #     processes={
    #         # 'cell_stub': cell_stub,
    #         "cell_wall": cell_wall
    #     },
    #     topology={
    #         'cell_wall': {
    #             'bulk_murein': ("bulk", 'CPD-12261[p]'),
    #             'shape' : ("shape",)
    #         },
    #         # 'cell_stub': {
    #         #     'global': ('global',),
    #         #     'bulk_murein': ("bulk", 'CPD-12261[p]'),
    #         # }
    #     },
    #     initial_state={
    #         'bulk': {
    #             'CPD-12261[p]': int(3e6),
    #         },
    #         'shape' : {
    #             'length' : 2 * units.um,
    #         }})
    #
    # sim.run_for(10)
    # data = sim.emitter.get_data()

    data = simulate_process(cell_wall, settings)
    # data = simulate_process(cell_stub, settings)
    # data = simulate_composite(test_composite, settings)
    fig = plot_variables(
        data,
        variables=[
            ("murein_state", "free_murein"),
            ("murein_state", "incorporated_murein"),
            ("wall_state", "lattice_rows"),
            ("wall_state", "lattice_cols"),
        ],
    )
    fig.tight_layout()

    os.makedirs("out/processes/cell_wall/", exist_ok=True)
    fig.savefig("out/processes/cell_wall/test.png")

    for t, lattice in enumerate(data["wall_state"]["lattice"]):
        print(f"Plotting t={t}...")
        fig, _ = plot_lattice(np.array(lattice), on_cylinder=True)
        fig.tight_layout()
        fig.savefig(f"out/processes/cell_wall/cell_wall_t{t}.png")
        print("Done.\n")


if __name__ == "__main__":
    main()