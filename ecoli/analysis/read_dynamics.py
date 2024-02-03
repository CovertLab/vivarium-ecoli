"""
Reads dynamics data for each of the nodes of a causality network from a single
simulation.
"""

import numpy as np
import os
import orjson
import hashlib
from tqdm import tqdm
import zipfile

from vivarium.library.dict_utils import get_value_from_path
from vivarium.core.emitter import data_from_database, get_experiment_database

from ecoli.processes.metabolism import (
    COUNTS_UNITS, VOLUME_UNITS, TIME_UNITS, MASS_UNITS)
from ecoli.analysis.network_components import (
    EDGELIST_JSON, Node, NODELIST_JSON, COUNT_UNITS, PROB_UNITS)
from ecoli.analysis.build_network import NODE_ID_SUFFIX

from wholecell.utils import units


MIN_TIMESTEPS = 41  # Minimum number of timesteps for a working visualization without modification
# REQUIRED_COLUMNS = [
# 	("BulkMolecules", "counts"),
# 	("ComplexationListener", "complexationEvents"),
# 	("EquilibriumListener", "reactionRates"),
# 	("FBAResults", "reactionFluxes"),
# 	("GrowthLimits", "net_charged"),
# 	("Main", "time"),
# 	("Mass", "cellMass"),
# 	("Mass", "dryMass"),
# 	("RNACounts", "mRNA_counts"),
#   ("RnaMaturationListener", "unprocessed_rnas_consumed"),
#   ("RnaSynthProb", "gene_copy_number"),
#   ("RnaSynthProb", "pPromoterBound"),
#   ("RnaSynthProb", "actual_rna_synth_prob_per_cistron"),
#   ("RnaSynthProb", "promoter_copy_number"),
#   ("RnaSynthProb", "n_bound_TF_per_TU"),
#   ("RnaSynthProb", "n_bound_TF_per_cistron"),
#   ("RnapData", "rnaInitEvent"),
#   ("RibosomeData", "probTranslationPerTranscript"),
# 	]


def get_safe_name(s):
    fname = str(int(hashlib.sha256(s.encode('utf-8')).hexdigest(), 16) % 10 **16)
    return fname


def array_timeseries(data, path, timeseries):
    """Converts data of the format {time: {path: value}}} to timeseries of the
    format {path: [value_1, value_2,...]}. Modifies timeseries in place."""
    path_timeseries = timeseries
    for key in path[:-1]:
        path_timeseries = path_timeseries.setdefault(key, {})
    accumulated_data = []
    for datum in data.values():
        path_data = get_value_from_path(datum, path)
        accumulated_data.append(path_data)
    path_timeseries[path[-1]] = np.array(accumulated_data)


def convert_dynamics(seriesOutDir, sim_data, node_list, edge_list, experiment_id):
    """Convert the sim's dynamics data to a Causality seriesOut.zip file."""

    if not experiment_id:
        experiment_id = input('Please provide an experiment id: ')

    # Retrieve the data directly from database
    db = get_experiment_database()
    query = [
        ('bulk',),
        ('listeners', 'mass', 'cell_mass'),
        ('listeners', 'mass', 'dry_mass'),
        ('listeners', 'rna_synth_prob', 'p_promoter_bound'),
        ('listeners', 'rna_synth_prob', 'actual_rna_synth_prob_per_cistron'),
        ('listeners', 'rna_synth_prob', 'promoter_copy_number'),
        ('listeners', 'rna_synth_prob', 'gene_copy_number'),
        ('listeners', 'rna_synth_prob', 'n_bound_TF_per_TU'),
        ('listeners', 'rna_synth_prob', 'n_bound_TF_per_cistron'),
        ('listeners', 'rna_counts', 'mRNA_counts'),
        ('listeners', 'rna_maturation_listener', 'unprocessed_rnas_consumed'),
        ('listeners', 'rnap_data', 'rna_init_event'),
        ('listeners', 'ribosome_data', 'actual_prob_translation_per_transcript'),
        ('listeners', 'complexation_listener', 'complexation_events'),
        ('listeners', 'fba_results', 'reaction_fluxes'),
        ('listeners', 'equilibrium_listener', 'reaction_rates'),
        ('listeners', 'growth_limits', 'net_charged')
    ]
    data, config = data_from_database(experiment_id, db, query=query)
    del data[0.0]

    timeseries = {}
    for path in query:
        array_timeseries(data, path, timeseries)
    timeseries['time'] = np.array(list(data.keys()))

    # Reshape arrays for number of bound transcription factors
    n_TU = len(sim_data.process.transcription.rna_data['id'])
    n_cistron = len(sim_data.process.transcription.cistron_data['id'])
    n_TF = len(sim_data.process.transcription_regulation.tf_ids)

    timeseries['listeners']['rna_synth_prob']['n_bound_TF_per_cistron'] =  np.array(
        timeseries['listeners']['rna_synth_prob']['n_bound_TF_per_cistron']).reshape(
            -1, n_cistron, n_TF)
    timeseries['listeners']['rna_synth_prob']['n_bound_TF_per_TU'] =  np.array(
        timeseries['listeners']['rna_synth_prob']['n_bound_TF_per_TU']).reshape(
            -1, n_TU, n_TF)
    
    conversion_coeffs = (
            timeseries['listeners']['mass']['dry_mass'] /
            timeseries['listeners']['mass']['cell_mass']
            * sim_data.constants.cell_density.asNumber(MASS_UNITS / VOLUME_UNITS)
    )
    timeseries['listeners']['fba_results']['reaction_fluxes_converted'] = (
        (COUNTS_UNITS / MASS_UNITS / TIME_UNITS) * (
            timeseries['listeners']['fba_results']['reaction_fluxes'].T /
                conversion_coeffs).T).asNumber(units.mmol/units.g/units.h)

    # Construct dictionaries of indexes where needed
    indexes = {}

    def build_index_dict(id_array):
        return {mol: i for i, mol in enumerate(id_array)}

    molecule_ids = config['metadata']['output_metadata']['bulk']
    indexes["BulkMolecules"] = build_index_dict(molecule_ids)

    gene_ids = sim_data.process.transcription.cistron_data['gene_id']
    indexes["Genes"] = build_index_dict(gene_ids)

    rna_ids = sim_data.process.transcription.rna_data['id']
    indexes["RNAs"] = build_index_dict(rna_ids)

    mRNA_ids = rna_ids[sim_data.process.transcription.rna_data['is_mRNA']]
    indexes["mRNAs"] = build_index_dict(mRNA_ids)

    translated_rna_ids = sim_data.process.translation.monomer_data['cistron_id']
    indexes["TranslatedRnas"] = build_index_dict(translated_rna_ids)

    # metabolism_rxn_ids = TableReader(
    # 	os.path.join(simOutDir, "FBAResults")).readAttribute("reactionIDs")
    metabolism_rxn_ids = config['metadata']['output_metadata'][
        'listeners']['fba_results']['reaction_fluxes']
    metabolism_rxn_ids = sim_data.process.metabolism.reaction_stoich.keys()
    indexes["MetabolismReactions"] = build_index_dict(metabolism_rxn_ids)

    complexation_rxn_ids = sim_data.process.complexation.ids_reactions
    indexes["ComplexationReactions"] = build_index_dict(complexation_rxn_ids)

    equilibrium_rxn_ids = sim_data.process.equilibrium.rxn_ids
    indexes["EquilibriumReactions"] = build_index_dict(equilibrium_rxn_ids)

    # unprocessed_rna_ids = TableReader(
    #     os.path.join(simOutDir, "RnaMaturationListener")).readAttribute("unprocessed_rna_ids")
    unprocessed_rna_ids = config['metadata']['output_metadata']['listeners'][
        'rna_maturation_listener']['unprocessed_rnas_consumed']
    indexes["UnprocessedRnas"] = build_index_dict(unprocessed_rna_ids)

    tf_ids = sim_data.process.transcription_regulation.tf_ids
    indexes["TranscriptionFactors"] = build_index_dict(tf_ids)

    trna_ids = sim_data.process.transcription.uncharged_trna_names
    indexes["Charging"] = build_index_dict(trna_ids)

    # Cache cell volume array (used for calculating concentrations)

    # volume = ((1.0 / sim_data.constants.cell_density) * (
    # 	units.fg * columns[("Mass", "cellMass")])).asNumber(units.L)
    volume = ((1.0 / sim_data.constants.cell_density) * (
        units.fg * timeseries['listeners']['mass']['cell_mass'])).asNumber(units.L)

    def dynamics_mapping(dynamics, safe):
        return [{
            'index': index,
            'units': dyn['units'],
            'type': dyn['type'],
            'filename': safe + '.json'}
            for index, dyn in enumerate(dynamics)]

    name_mapping = {}

    def build_dynamics(node_dict):
        node = Node()
        node.node_id = node_dict['ID']
        node.node_type = node_dict['type']
        reader = TYPE_TO_READER_FUNCTION.get(node.node_type)
        if reader:
            # reader(sim_data, node, node.node_id, columns, indexes, volume)
            reader(sim_data, node, node.node_id, indexes, volume, timeseries)
        return node

    def save_node(node, name_mapping):
        if node.node_id in name_mapping:
            # Skip duplicates. Why are there duplicates? --check_sanity finds them.
            return

        dynamics_path = get_safe_name(node.node_id)
        dynamics = node.dynamics_dict()
        dynamics_json = orjson.dumps(dynamics, option=orjson.OPT_SERIALIZE_NUMPY)

        zf.writestr(os.path.join('series', dynamics_path + '.json'), dynamics_json)

        name_mapping[str(node.node_id)] = dynamics_mapping(dynamics, dynamics_path)

    # ZIP_BZIP2 saves 14% bytes vs. ZIP_DEFLATED but takes  +70 secs.
    # ZIP_LZMA  saves 19% bytes vs. ZIP_DEFLATED but takes +260 sec.
    # compresslevel=9 saves very little space.
    zip_name = os.path.join(seriesOutDir, 'seriesOut.zip')
    with zipfile.ZipFile(zip_name, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for node_dict in tqdm(node_list):
            node = build_dynamics(node_dict)
            save_node(node, name_mapping)
        save_node(time_node(timeseries), name_mapping)

        zf.writestr('series.json', orjson.dumps(name_mapping))
        zf.writestr(NODELIST_JSON, orjson.dumps(node_list))
        zf.writestr(EDGELIST_JSON, orjson.dumps(edge_list, option=orjson.OPT_SERIALIZE_NUMPY))


def time_node(timeseries):
    time_node = Node()
    attr = {
        'node_class': 'time',
        'node_type': 'time',
        'node_id': 'time',
        }
    time_node.read_attributes(**attr)
    if len(timeseries['time']) < MIN_TIMESTEPS:
        time = np.array([0.0 + (2 * i) for i in range(MIN_TIMESTEPS)])
    else:
        time = timeseries['time']
    # time = columns[("Main", "time")]
    dynamics = {
        'time': time,
        }
    dynamics_units = {
        'time': 's',
        }

    time_node.read_dynamics(dynamics, dynamics_units)
    return time_node


def read_global_dynamics(sim_data, node, node_id, indexes, volume, timeseries):
    """
    Reads global dynamics from simulation output.
    """

    cell_mass = timeseries['listeners']['mass']['cell_mass']

    if node_id == "cell_mass":
        dynamics = {
            "mass": cell_mass,
            }
        dynamics_units = {
            "mass": "fg",
            }

    elif node_id == "cell_volume":
        cell_volume = ((1.0 / sim_data.constants.cell_density) * (
                    units.fg * cell_mass)).asNumber(units.L)
        dynamics = {
            'volume': cell_volume,
            }
        dynamics_units = {
            'volume': 'L',
            }

    else:
        return

    node.read_dynamics(dynamics, dynamics_units)


def read_gene_dynamics(sim_data, node, node_id, indexes, volume, timeseries):
    """
    Reads dynamics data for gene nodes from simulation output.
    """
    gene_index = indexes["Genes"][node_id]
    dynamics = {
        # "transcription probability": columns[("RnaSynthProb", "actual_rna_synth_prob_per_cistron")][:, gene_index],
        "transcription probability": timeseries['listeners']['rna_synth_prob'][
            'actual_rna_synth_prob_per_cistron'][:, gene_index],
        # "gene copy number": columns[("RnaSynthProb", "gene_copy_number")][:, gene_index],
        "gene copy number": timeseries['listeners']['rna_synth_prob']['gene_copy_number'][:, gene_index],
        }
    dynamics_units = {
        "transcription probability": PROB_UNITS,
        "gene copy number": COUNT_UNITS,
        }
    node.read_dynamics(dynamics, dynamics_units)


def read_rna_dynamics(sim_data, node, node_id, indexes, volume, timeseries):
    """
    Reads dynamics data for transcript (RNA) nodes from simulation output.
    """
    # If RNA is an mRNA, get counts from mRNA counts listener
    if node_id in indexes["mRNAs"]:
        # counts = columns[("mRNACounts", "mRNA_counts")][:, indexes["mRNAs"][node_id]]
        counts = timeseries['listeners']['rna_counts']['mRNA_counts'][:, indexes["mRNAs"][node_id]]
    # If not, get counts from bulk molecules listener
    else:
        # counts = columns[("BulkMolecules", "counts")][:, indexes["BulkMolecules"][node_id]]
        counts = timeseries['bulk'][:, indexes["BulkMolecules"][node_id]]

    dynamics = {
        "counts": counts,
        }
    dynamics_units = {
        "counts": COUNT_UNITS,
        }

    node.read_dynamics(dynamics, dynamics_units)


def read_protein_dynamics(sim_data, node, node_id, indexes, volume, timeseries):
    """
    Reads dynamics data for monomer/complex nodes from a simulation output.
    """
    count_index = indexes["BulkMolecules"][node_id]
    # counts = columns[("BulkMolecules", "counts")][:, count_index]
    counts = timeseries['bulk'][:, count_index]
    concentration = (((1 / sim_data.constants.n_avogadro) * counts) / (units.L * volume)).asNumber(units.mmol / units.L)

    dynamics = {
        'counts': counts,
        'concentration': concentration,
        }
    dynamics_units = {
        'counts': COUNT_UNITS,
        'concentration': 'mmol/L',
        }
    node.read_dynamics(dynamics, dynamics_units)


def read_metabolite_dynamics(sim_data, node, node_id, indexes, volume, timeseries):
    """
    Reads dyanmics data for metabolite nodes from a simulation output.
    """
    try:
        count_index = indexes["BulkMolecules"][node_id]
    except (KeyError, IndexError):
        return  # Metabolite not being modeled
    # counts = columns[("BulkMolecules", "counts")][:, count_index]
    counts = timeseries['bulk'][:, count_index]
    concentration = (((1 / sim_data.constants.n_avogadro) * counts) / (units.L * volume)).asNumber(units.mmol / units.L)

    dynamics = {
        'counts': counts,
        'concentration': concentration,
        }
    dynamics_units = {
        'counts': COUNT_UNITS,
        'concentration': 'mmol/L',
        }

    node.read_dynamics(dynamics, dynamics_units)


def read_transcription_dynamics(sim_data, node, node_id, indexes, volume, timeseries):
    """
    Reads dynamics data for transcription nodes from simulation output.
    """
    rna_id = node_id.split(NODE_ID_SUFFIX["transcription"])[0]
    rna_idx = indexes['RNAs'][rna_id + '[c]']
    dynamics = {
        # "transcription initiations": columns[("RnapData", "rnaInitEvent")][:, rna_idx],
        "transcription initiations": timeseries['listeners']['rnap_data']['rna_init_event'][:, rna_idx],
        # "promoter copy number": columns[("RnaSynthProb", "promoter_copy_number")][:, rna_idx],
        "promoter copy number": timeseries['listeners']['rna_synth_prob']["promoter_copy_number"][:, rna_idx],
    }
    dynamics_units = {
        "transcription initiations": COUNT_UNITS,
        }

    node.read_dynamics(dynamics, dynamics_units)


def read_translation_dynamics(sim_data, node, node_id, indexes, volume, timeseries):
    """
    Reads dynamics data for translation nodes from a simulation output.
    """
    rna_id = node_id.split(NODE_ID_SUFFIX["translation"])[0] + "_RNA"
    translation_idx = indexes["TranslatedRnas"][rna_id]
    dynamics = {
        'translation probability': timeseries['listeners']['ribosome_data']['actual_prob_translation_per_transcript']
            [:, translation_idx],
        }
    dynamics_units = {
        'translation probability': PROB_UNITS,
        }

    node.read_dynamics(dynamics, dynamics_units)


def read_complexation_dynamics(sim_data, node, node_id, indexes, volume, timeseries):
    """
    Reads dynamics data for complexation nodes from a simulation output.
    """
    reaction_idx = indexes["ComplexationReactions"][node_id]
    dynamics = {
        'complexation events': timeseries['listeners']['complexation_listener']['complexation_events'][:, reaction_idx],
        # 'complexation events': columns[("ComplexationListener", "complexationEvents")][:, reaction_idx],
        }
    dynamics_units = {
        'complexation events': COUNT_UNITS,
        }

    node.read_dynamics(dynamics, dynamics_units)


def read_rna_maturation_dynamics(sim_data, node, node_id, indexes, volume, timeseries):
    """
    Reads dynamics data for RNA maturation nodes from a simulation output.
    """
    reaction_idx = indexes["UnprocessedRnas"][node_id[:-4] + '[c]']

    dynamics = {
        # 'RNA maturation events': columns[("RnaMaturationListener", "unprocessed_rnas_consumed")][:, reaction_idx],
        'RNA maturation events': timeseries["listeners"]["rna_maturation_listener"][
            "unprocessed_rnas_consumed"][:, reaction_idx],
        }
    dynamics_units = {
        'RNA maturation events': COUNT_UNITS,
        }

    node.read_dynamics(dynamics, dynamics_units)


def read_metabolism_dynamics(sim_data, node, node_id, indexes, volume, timeseries):
    """
    Reads dynamics data for metabolism nodes from a simulation output.
    """
    reaction_idx = indexes["MetabolismReactions"][node_id]
    dynamics = {
        # 'flux': columns[("FBAResults", "reactionFluxesConverted")][:, reaction_idx],
        'flux': timeseries['listeners']['fba_results'][
            'reaction_fluxes_converted'][:, reaction_idx],
    }
    dynamics_units = {
        'flux': 'mmol/gCDW/h',
        }

    node.read_dynamics(dynamics, dynamics_units)


def read_equilibrium_dynamics(sim_data, node, node_id, indexes, volume, timeseries):
    """
    Reads dynamics data for equilibrium nodes from a simulation output.
    """
    # TODO (ggsun): Fluxes for 2CS reactions are not being listened to.
    try:
        reaction_idx = indexes["EquilibriumReactions"][node_id]
    except (KeyError, IndexError):
        return  # 2CS reaction

    dynamics = {
        # 'reaction rate': columns[("EquilibriumListener", "reactionRates")][:, reaction_idx],
        'reaction rate': timeseries['listeners']['equilibrium_listener']['reaction_rates'][:, reaction_idx],
    }
    dynamics_units = {
        'reaction rate': 'rxns/s',
        }

    node.read_dynamics(dynamics, dynamics_units)


def read_regulation_dynamics(sim_data, node, node_id, indexes, volume, timeseries):
    """
    Reads dynamics data for regulation nodes from a simulation output.
    """
    tf_id, gene_id, _ = node_id.split("_")
    gene_idx = indexes["Genes"][gene_id]
    tf_idx = indexes["TranscriptionFactors"][tf_id]

    bound_tf_array = timeseries['listeners']['rna_synth_prob']['n_bound_TF_per_cistron']

    dynamics = {
        # 'bound TFs': columns[("RnaSynthProb", "n_bound_TF_per_TU")][:, gene_idx, tf_idx],
        'bound TFs': bound_tf_array[:, gene_idx, tf_idx],
    }
    dynamics_units = {
        'bound TFs': COUNT_UNITS,
        }

    node.read_dynamics(dynamics, dynamics_units)


def read_tf_binding_dynamics(sim_data, node, node_id, indexes, volume, timeseries):
    """
    Reads dynamics data for TF binding nodes from a simulation output.
    """
    tf_id, _ = node_id.split("-bound")
    tf_idx = indexes["TranscriptionFactors"][tf_id]

    dynamics = {
        # 'bound TFs': columns[("RnaSynthProb", "n_bound_TF_per_TU")][:, :, tf_idx].sum(axis=1),
        'bound TFs': timeseries['listeners']['rna_synth_prob']['n_bound_TF_per_TU'][:, :, tf_idx].sum(axis=1),
    }
    dynamics_units = {
        'bound TFs': COUNT_UNITS,
        }

    node.read_dynamics(dynamics, dynamics_units)


def read_charging_dynamics(sim_data, node, node_id, indexes, volume, timeseries):
    """
    Reads dynamics data for charging nodes from a simulation output.
    """
    rna = '{}[c]'.format(node_id.split(' ')[0])
    rna_idx = indexes["Charging"][rna]
    dynamics = {
        # 'reaction rate': columns[("GrowthLimits", "net_charged")][:, rna_idx]
        'reaction rate': timeseries['listeners']['growth_limits']['net_charged'][:, rna_idx]
        }
    dynamics_units = {
        'reaction rate': 'rxns/s',
        }

    node.read_dynamics(dynamics, dynamics_units)


TYPE_TO_READER_FUNCTION = {
    "Global": read_global_dynamics,
    "Gene": read_gene_dynamics,
    "RNA": read_rna_dynamics,
    "Protein": read_protein_dynamics,
    "Complex": read_protein_dynamics,
    "Metabolite": read_metabolite_dynamics,
    "Transcription": read_transcription_dynamics,
    "Translation": read_translation_dynamics,
    "Complexation": read_complexation_dynamics,
    "Equilibrium": read_equilibrium_dynamics,
    "RNA Maturation": read_rna_maturation_dynamics,
    "Metabolism": read_metabolism_dynamics,
    "Transport": read_metabolism_dynamics,
    "Regulation": read_regulation_dynamics,
    "TF Binding": read_tf_binding_dynamics,
    "Charging": read_charging_dynamics,
    }
