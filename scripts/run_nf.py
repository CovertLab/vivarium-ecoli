import argparse
import os

from ecoli.composites.ecoli_configs import CONFIG_DIR_PATH
from ecoli.experiments.ecoli_master_sim import SimConfig

SIM_TAG = "seed_{seed}_gen_{gen}_cell_{cell}"
SIM_GEN_0_INC = "include {{ sim_gen_0 as sim_{name} }} from './sim'"
SIM_INC = "include {{ sim as sim_{name} }} from './sim'"
SIM_GEN_0_FLOW = ("\tsim_{name}(params.config, "
    "variant_ch.combine([{seed}]).combine([0]), {seed}, 0)")
SIM_FLOW = ("\tsim_{name}(sim_{parent}.out.config, sim_{parent}.out.next_gen, "
    "sim_{parent}.out.{daughter}, {seed}, {cell})")

ALL_SIM_CHANNEL = """
    {task_one}
        .mix({other_tasks})
        .set {{ sim_ch }}
"""
MULTIGEN_CHANNEL = """
    sim_ch
        .groupTuple(by: [1, 2], size: {size})
        .set {{ multigen_ch }}
"""
COHORT_CHANNEL = """
    sim_ch
        .groupTuple(by: 1, size: {size})
        .set {{ cohort_ch }}
"""
VARIANT_CHANNEL = """
    sim_ch
        .groupTuple(by: [5])
        .set {{ variant_ch }}
"""


def generate_colony(seeds: int):
    """
    Create strings to import and compose Nextflow processes for colony sims.
    """
    return [], []


def generate_lineage(seed: int, n_init_sims: int, generations: int, 
    single_daughters: bool, analysis_config: dict[str, list[str]]):
    """
    Create strings to import and compose Nextflow processes for lineage sims:
    cells that divide for a number of generations but do not interact. Also
    contains import statements and workflow jobs for analysis scripts.

    Args:
        seed: First seed for first sim
        n_init_sims: Number of sims to initialize with different seeds
        generations: Number of generations to run for each seed
        single_daughters: If True, only simulate one daughter cell each gen
        analysis_config: Dictionary with any of the following keys::

            {
                'variant': analyses to run on output of all cells combined,
                'cohort': analyses to run on output grouped by variant,
                'multigen': analyses to run on output grouped by variant & seed,
                'single': analyses to run on output for each individual cell,
                'parca': analyses to run on parameter calculator output
            }

    Returns:
        2-element tuple containing
    
        - **sim_imports**: All `include` statements for Nextflow sim processes
        - **sim_workflow**: Fully composed workflow for entire lineage
    """
    sim_imports = []
    sim_workflow = []

    all_sim_tasks = []
    for seed in range(seed, seed + n_init_sims):
        for gen in range(generations):
            for cell in (range(2**gen) if not single_daughters else [0]):
                name = SIM_TAG.format(seed=seed, gen=gen, cell=cell)

                # Compile list of metadata outputs for all sim tasks
                all_sim_tasks.append(f'sim_{name}.out.metadata')
                
                # Handle special case of 1st generation
                if gen == 0:
                    sim_imports.append(SIM_GEN_0_INC.format(name=name))
                    sim_workflow.append(SIM_GEN_0_FLOW.format(name=name, seed=seed))
                    continue
                
                sim_imports.append(SIM_INC.format(name=name))
                # Cell 0 has daughters 0 & 1 in next gen, 1 has 2 & 3, etc.
                parent = SIM_TAG.format(seed=seed, gen=gen-1, cell=cell//2)
                daughter = 'd1'
                if cell % 2 == 1 and not single_daughters:
                    daughter = 'd2'
                sim_workflow.append(SIM_FLOW.format(name=name, parent=parent,
                    daughter=daughter, seed=seed+gen+cell, cell=cell))

    # Channel that combines metadata for all sim tasks
    sim_workflow.append(ALL_SIM_CHANNEL.format(task_one=all_sim_tasks[0],
        other_tasks=', '.join(all_sim_tasks[1:])))

    if analysis_config.get('variant', False):
        # Channel that groups all sim tasks
        sim_workflow.append(VARIANT_CHANNEL.format(
            size=int(len(all_sim_tasks) / n_init_sims)))
        sim_workflow.append("\tanalysis_variant(params.config, kb, variant_ch)")
        sim_imports.append("include { analysis_variant } from './sim'")
    
    if analysis_config.get('cohort', False):
        # Channel that groups sim tasks by variant sim_data
        sim_workflow.append(COHORT_CHANNEL.format(size=len(all_sim_tasks)))
        sim_workflow.append("\tanalysis_cohort(params.config, kb, cohort_ch)")
        sim_imports.append("include { analysis_cohort } from './sim'")
    
    if analysis_config.get('multigen', False):
        # Channel that groups sim tasks by variant sim_data and initial seed
        sim_workflow.append(MULTIGEN_CHANNEL.format(
            size=int(len(all_sim_tasks) / n_init_sims)))
        sim_workflow.append("\tanalysis_multigen(params.config, kb, multigen_ch)")
        sim_imports.append("include { analysis_multigen } from './sim'")
    
    if analysis_config.get('single', False):
        sim_workflow.append("\tanalysis_single(params.config, kb, sim_ch)")
        sim_imports.append("include { analysis_single } from './sim'")
    
    if analysis_config.get('parca', False):
        sim_workflow.append("\tanalysis_parca(params.config, kb)")

    return sim_imports, sim_workflow


def generate_code(config):
    seed = config.get('seed', 0)
    lineage = config.get('lineage', {})
    n_init_sims = lineage.get('n_init_sims')
    if lineage:
        generations = lineage.get('generations', 1)
        single_daughters = lineage.get('single_daughters', True)
        sim_imports, sim_workflow = generate_lineage(
            seed, n_init_sims, generations, single_daughters,
            config.get('analysis', {}))
    else:
        sim_imports, sim_workflow = generate_colony(seed, n_init_sims)
    return '\n'.join(sim_imports), '\n'.join(sim_workflow)       


def main():
    parser = argparse.ArgumentParser()
    default_config = os.path.join(CONFIG_DIR_PATH, 'default.json')
    parser.add_argument(
        '--config', action='store',
        default=default_config,
        help=(
            'Path to configuration file for the simulation. '
            'All key-value pairs in this file will be applied on top '
            f'of the options defined in {default_config}.'))
    config = SimConfig(parser=parser)
    config.update_from_cli()

    sim_imports, sim_workflow = generate_code(config)

    nf_template_path = os.path.join(os.path.dirname(__file__), 'nextflow', 'template.nf')
    with open(nf_template_path, 'r') as f:
        nf_template = f.readlines()
    nf_template = ''.join(nf_template)
    nf_template = nf_template.replace("IMPORTS", sim_imports)
    nf_template = nf_template.replace("WORKFLOW", sim_workflow)
    with open(os.path.join(os.path.dirname(__file__), 'nextflow', 'main.nf'), 'w') as f:
        f.writelines(nf_template)


if __name__ == '__main__':
    main()
