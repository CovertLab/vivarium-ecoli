import argparse
import os
import subprocess

from ecoli.composites.ecoli_configs import CONFIG_DIR_PATH
from ecoli.experiments.ecoli_master_sim import SimConfig

SIM_TAG = "Seed{seed}Gen{gen}Cell{cell}"
SIM_GEN_0_INC = "include {{ simGen0 as sim{name} }} from './sim'"
SIM_INC = "include {{ sim as sim{name} }} from './sim'"
SIM_GEN_0_FLOW = ("\tsim{name}(params.config, "
    "variantCh.combine([{seed}]).combine([0]), {seed}, 0)")
SIM_FLOW = ("\tsim{name}(sim{parent}.out.config, sim{parent}.out.nextGen, "
    "sim{parent}.out.{daughter}, {seed}, {cell})")

ALL_SIM_CHANNEL = """
    {task_one}
        .mix({other_tasks})
        .set {{ simCh }}
"""
MULTIGEN_CHANNEL = """
    simCh
        .groupTuple(by: [1, 2], size: {size})
        .set {{ multigenCh }}
"""
COHORT_CHANNEL = """
    simCh
        .groupTuple(by: 1, size: {size})
        .set {{ cohortCh }}
"""
VARIANT_CHANNEL = """
    simCh
        .groupTuple(by: [5])
        .set {{ variantCh }}
"""


def generate_colony(seeds: int):
    """
    Create strings to import and compose Nextflow processes for colony sims.
    """
    return [], []


def generate_lineage(seed: int, n_init_sims: int, generations: int, 
    single_daughters: bool, analysis_config: dict[str, dict[str, dict]]]):
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

            Each key corresponds to a mapping from analysis name (as defined
            in ``ecol/analysis/__init__.py``) to keyword arguments.

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
                all_sim_tasks.append(f'sim{name}.out.metadata')
                
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
        sim_workflow.append("\tanalysisVariant(params.config, kb, variantCh)")
        sim_imports.append("include { analysisVariant } from './analysis'")
    
    if analysis_config.get('cohort', False):
        # Channel that groups sim tasks by variant sim_data
        sim_workflow.append(COHORT_CHANNEL.format(size=len(all_sim_tasks)))
        sim_workflow.append("\tanalysisCohort(params.config, kb, cohortCh)")
        sim_imports.append("include { analysisCohort } from './analysis'")
    
    if analysis_config.get('multigen', False):
        # Channel that groups sim tasks by variant sim_data and initial seed
        sim_workflow.append(MULTIGEN_CHANNEL.format(
            size=int(len(all_sim_tasks) / n_init_sims)))
        sim_workflow.append("\tanalysisMultigen(params.config, kb, multigenCh)")
        sim_imports.append("include { analysisMultigen } from './analysis'")
    
    if analysis_config.get('single', False):
        sim_workflow.append("\tanalysisSingle(params.config, kb, simCh)")
        sim_imports.append("include { analysisSingle } from './analysis'")
    
    if analysis_config.get('parca', False):
        sim_workflow.append("\tanalysisParca(params.config, kb)")

    return sim_imports, sim_workflow


def generate_code(config):
    seed = config.get('seed', 0)
    generations = config.get('generations', 0)
    if generations:
        n_init_sims = config.get('n_init_sims')
        single_daughters = config.get('single_daughters', True)
        sim_imports, sim_workflow = generate_lineage(
            seed, n_init_sims, generations, single_daughters,
            config.get('analysis_options', {}))
    else:
        sim_imports, sim_workflow = generate_colony(seed, n_init_sims)
    return '\n'.join(sim_imports), '\n'.join(sim_workflow)


def build_runtime_image(image_name):
    build_script = os.path.join(os.path.dirname(__file__),
                                'container', 'build-runtime.sh')
    subprocess.run([build_script, '-r', image_name], check=True)


def build_wcm_image(image_name, runtime_image_name):
    build_script = os.path.join(os.path.dirname(__file__),
                                'container', 'build-wcm.sh')
    subprocess.run([build_script, '-w', image_name,
                    '-l', runtime_image_name], check=True)


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

    cloud_config = config.get('gcloud', None)
    if cloud_config is not None:
        # Add logic for starting MongoDB VM
        build_runtime_image = cloud_config.get('build_runtime_image', False)
        runtime_image_name = cloud_config.get('runtime_image_name', None)
        if build_runtime_image:
            if runtime_image_name is None:
                raise RuntimeError('Must supply name for runtime image.')
            build_runtime_image(runtime_image_name)
        build_wcm_image = cloud_config.get('build_wcm_image', False)
        wcm_image_name = cloud_config.get('wcm_image_name', None)
        if wcm_image_name is None:
            raise RuntimeError('Must supply name for WCM image.')
        if build_wcm_image:
            if runtime_image_name is None:
                raise RuntimeError('Must supply name for runtime image.')
            build_wcm_image(wcm_image_name, runtime_image_name)
        nf_config = os.path.join(os.path.dirname(__file__),
                                 'nextflow', 'config.template')
        with open(nf_config, 'r') as f:
            nf_config = f.readlines()
        nf_config = nf_config.replace("IMAGE_NAME", wcm_image_name)
        config_path = os.path.join(os.path.dirname(__file__),
                                   'nextflow', 'nextflow.config')
        with open(config_path, 'w') as f:
            f.writelines(nf_config)

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
