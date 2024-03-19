import argparse
import os
import pickle
import time

from ecoli.composites.ecoli_configs import CONFIG_DIR_PATH
from ecoli.experiments.ecoli_master_sim import SimConfig
from reconstruction.ecoli.knowledge_base_raw import KnowledgeBaseEcoli
from reconstruction.ecoli.fit_sim_data_1 import fitSimData_1
from validation.ecoli.validation_data_raw import ValidationDataRawEcoli
from validation.ecoli.validation_data import ValidationDataEcoli
from wholecell.utils import constants
import wholecell.utils.filepath as fp


def run_parca(config):
    # Make output directory
    kb_directory = fp.makedirs(config['outdir'], constants.KB_DIR)

    raw_data_file = os.path.join(kb_directory, constants.SERIALIZED_RAW_DATA)
    sim_data_file = os.path.join(
        kb_directory, constants.SERIALIZED_SIM_DATA_FILENAME)
    raw_validation_data_file = os.path.join(
        kb_directory, constants.SERIALIZED_RAW_VALIDATION_DATA)
    validation_data_file = os.path.join(
        kb_directory, constants.SERIALIZED_VALIDATION_DATA)


    print(f"{time.ctime()}: Instantiating raw_data with operons={config['operons']}")
    raw_data = KnowledgeBaseEcoli(
        operons_on=config['operons'],
        remove_rrna_operons=config['remove_rrna_operons'],
        remove_rrff=config['remove_rrff'])
    print(f"{time.ctime()}: Saving raw_data")
    with open(raw_data_file, 'wb') as f:
        pickle.dump(raw_data, f)
    
    print(f"{time.ctime()}: Instantiating sim_data with operons={config['operons']}")
    sim_data = fitSimData_1(
        raw_data=raw_data, cpus=config['cpus'], debug=config['debug_parca'],
        load_intermediate=config['load_intermediate'],
        save_intermediates=config['save_intermediates'],
        intermediates_directory=config['intermediates_directory'],
        variable_elongation_transcription=config['variable_elongation_transcription'],
        variable_elongation_translation=config['variable_elongation_translation'],
        disable_ribosome_capacity_fitting=(not config['ribosome_fitting']),
        disable_rnapoly_capacity_fitting=(not config['rnapoly_fitting'])
    )
    print(f"{time.ctime()}: Saving sim_data")
    with open(sim_data_file, 'wb') as f:
        pickle.dump(sim_data, f)

    print(f"{time.ctime()}: Instantiating raw_validation_data")
    raw_validation_data = ValidationDataRawEcoli()
    print(f"{time.ctime()}: Saving raw_validation_data")
    with open(raw_validation_data_file, 'wb') as f:
        pickle.dump(raw_validation_data, f)
    
    print(f"{time.ctime()}: Instantiating validation_data")
    validation_data = ValidationDataEcoli()
    validation_data.initialize(raw_validation_data, raw_data)
    print(f"{time.ctime()}: Saving validation_data")
    with open(validation_data_file, 'wb') as f:
        pickle.dump(validation_data, f)


def main():
    parser = argparse.ArgumentParser(description='run_parca')
    default_config = os.path.join(CONFIG_DIR_PATH, 'default.json')
    parser.add_argument(
        '--config', action='store',
        default=default_config,
        help=(
            'Path to configuration file for the simulation. '
            'All key-value pairs in this file will be applied on top '
            f'of the options defined in {default_config}.'))
    parser.add_argument('-c', '--cpus', type=int, default=1,
        help='The number of CPU processes to use. Default = 1.')
    parser.add_argument('-o', '--outdir', type=str,
        default='reconstruction/sim_data', help='Directory to hold ParCa'
        ' output kb folder. Default = reconstruction/sim_data')
    parser.add_argument('--operons', action=argparse.BooleanOptionalAction,
        default=True, help='Turn operons on (polycistronic).')
    parser.add_argument('--ribosome-fitting', default=True,
        action=argparse.BooleanOptionalAction,
        help='Fit ribosome expression to protein synthesis demands.')
    parser.add_argument('--rnapoly-fitting', default=True,
        action=argparse.BooleanOptionalAction,
        help='Fit RNA polymerase expression to protein synthesis demands.')
    parser.add_argument('--remove-rrna-operons', action='store_true',
        help='Remove the seven rRNA operons. Does not have any effect if'
        ' --no-operons specified.')
    parser.add_argument('--remove-rrff', action='store_true',
        help='Remove the rrfF gene. If operons are enabled,'
        ' removes the rrfF gene from the rrnD operon.')
    parser.add_argument('--debug-parca', action='store_true',
        help='Make Parca calculate only one arbitrarily-chosen transcription'
        ' factor condition when adjusting gene expression levels, leaving'
        ' the other TFs at their input levels for faster Parca debugging.'
        ' DO NOT USE THIS FOR A MEANINGFUL SIMULATION.')
    parser.add_argument('--load-intermediate', default=None, type=str,
        help='The function in the parca to load (skips functions that would'
        ' have run before the function). Must run with --save-intermediates'
        ' first.')
    parser.add_argument('--save-intermediates', action='store_true',
        help='If set, saves sim_data and cell_specs at intermediate'
        ' function calls in the parca.')
    parser.add_argument('--intermediates-directory', default='', type=str,
        help='Directory to save or load intermediate sim_data and cell_specs'
        ' results from if --load-intermediate or --save-intermediates'
        ' are set.')
    parser.add_argument('--variable-elongation-transcription', default=True,
        action=argparse.BooleanOptionalAction,
        help='Use a different elongation rate for different transcripts'
        ' (currently increases rates for rRNA). Usually set this'
        ' consistently between runParca and runSim.')
    parser.add_argument('--variable-elongation-translation', default=False,
        action=argparse.BooleanOptionalAction,
        help='Use a different elongation rate for different polypeptides'
        ' (currently increases rates for ribosomal proteins).'
        ' Usually set this consistently between runParca and runSim.')
    
    config = SimConfig(parser=parser)
    config.update_from_cli()
    config = config.to_dict()
    # ParCa options are defined under `parca_options` key in config JSON
    # Merge these with CLI arguments, which take precedence
    parca_options = config.pop('parca_options')
    config = {**parca_options, **config}
    run_parca(config)


if __name__ == '__main__':
    main()
