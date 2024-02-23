process run_parca {
    // Run ParCa using parca_options from config JSON
    publishDir "${params.publish_dir}/parca"

    input:
    path config

    output:
    path "kb"

    script:
    """
    python ${params.project_root}/scripts/run_parca.py -c $config -o \$(pwd)
    """

    stub:
    """
    mkdir kb
    echo "Mock sim_data" > kb/simData.cPickle
    echo "Mock raw_data" > kb/rawData.cPickle
    echo "Mock raw_validation_data" > kb/rawValidationData.cPickle
    echo "Mock validation_data" > kb/validationData.cPickle
    """
}

process create_variants {
    // Parse variants in config JSON to generate variants
    publishDir "${params.publish_dir}/variant_sim_data"

    input:
    path config
    path kb

    output:
    path "*.cPickle", emit: variant_sim_data
    path "metadata.json", emit: variant_metadata

    script:
    """
    python ${params.project_root}/scripts/create_variants.py -c $config --kb $kb -o \$(pwd)
    """

    stub:
    """
    cp $kb/simData.cPickle variant_1.cPickle
    echo "Mock variant 1" >> variant_1.cPickle
    cp $kb/simData.cPickle variant_2.cPickle
    echo "Mock variant 2" >> variant_2.cPickle
    echo "Mock metadata.json" > metadata.json
    """
}

process analysis_parca {
    publishDir "${params.publish_dir}/parca"

    input:
    path config
    path kb

    output:
    path 'plots/*'

    script:
    """
    python ${params.project_root}/scripts/analysis.py -c $config \
        --sim-data-path=$kb/simData.cPickle \
        --validation-data-path=$kb/validationData.cPickle \
        --parca -o \$(pwd)
    """

    stub:
    """
    mkdir plots
    echo -e "$config\n\n$kb" > plots/test.txt
    """
}

include { sim_gen_0 as sim_seed_0_gen_0_cell_0 } from './sim.nf'
include { sim as sim_seed_0_gen_1_cell_0 } from './sim.nf'
include { sim as sim_seed_0_gen_2_cell_0 } from './sim.nf'
include { sim as sim_seed_0_gen_3_cell_0 } from './sim.nf'
include { sim as sim_seed_0_gen_4_cell_0 } from './sim.nf'
include { sim as sim_seed_0_gen_5_cell_0 } from './sim.nf'
include { sim as sim_seed_0_gen_6_cell_0 } from './sim.nf'
include { sim as sim_seed_0_gen_7_cell_0 } from './sim.nf'
include { sim as sim_seed_0_gen_8_cell_0 } from './sim.nf'
include { sim as sim_seed_0_gen_9_cell_0 } from './sim.nf'
include { sim_gen_0 as sim_seed_1_gen_0_cell_0 } from './sim.nf'
include { sim as sim_seed_1_gen_1_cell_0 } from './sim.nf'
include { sim as sim_seed_1_gen_2_cell_0 } from './sim.nf'
include { sim as sim_seed_1_gen_3_cell_0 } from './sim.nf'
include { sim as sim_seed_1_gen_4_cell_0 } from './sim.nf'
include { sim as sim_seed_1_gen_5_cell_0 } from './sim.nf'
include { sim as sim_seed_1_gen_6_cell_0 } from './sim.nf'
include { sim as sim_seed_1_gen_7_cell_0 } from './sim.nf'
include { sim as sim_seed_1_gen_8_cell_0 } from './sim.nf'
include { sim as sim_seed_1_gen_9_cell_0 } from './sim.nf'
include { sim_gen_0 as sim_seed_2_gen_0_cell_0 } from './sim.nf'
include { sim as sim_seed_2_gen_1_cell_0 } from './sim.nf'
include { sim as sim_seed_2_gen_2_cell_0 } from './sim.nf'
include { sim as sim_seed_2_gen_3_cell_0 } from './sim.nf'
include { sim as sim_seed_2_gen_4_cell_0 } from './sim.nf'
include { sim as sim_seed_2_gen_5_cell_0 } from './sim.nf'
include { sim as sim_seed_2_gen_6_cell_0 } from './sim.nf'
include { sim as sim_seed_2_gen_7_cell_0 } from './sim.nf'
include { sim as sim_seed_2_gen_8_cell_0 } from './sim.nf'
include { sim as sim_seed_2_gen_9_cell_0 } from './sim.nf'
include { sim_gen_0 as sim_seed_3_gen_0_cell_0 } from './sim.nf'
include { sim as sim_seed_3_gen_1_cell_0 } from './sim.nf'
include { sim as sim_seed_3_gen_2_cell_0 } from './sim.nf'
include { sim as sim_seed_3_gen_3_cell_0 } from './sim.nf'
include { sim as sim_seed_3_gen_4_cell_0 } from './sim.nf'
include { sim as sim_seed_3_gen_5_cell_0 } from './sim.nf'
include { sim as sim_seed_3_gen_6_cell_0 } from './sim.nf'
include { sim as sim_seed_3_gen_7_cell_0 } from './sim.nf'
include { sim as sim_seed_3_gen_8_cell_0 } from './sim.nf'
include { sim as sim_seed_3_gen_9_cell_0 } from './sim.nf'
include { sim_gen_0 as sim_seed_4_gen_0_cell_0 } from './sim.nf'
include { sim as sim_seed_4_gen_1_cell_0 } from './sim.nf'
include { sim as sim_seed_4_gen_2_cell_0 } from './sim.nf'
include { sim as sim_seed_4_gen_3_cell_0 } from './sim.nf'
include { sim as sim_seed_4_gen_4_cell_0 } from './sim.nf'
include { sim as sim_seed_4_gen_5_cell_0 } from './sim.nf'
include { sim as sim_seed_4_gen_6_cell_0 } from './sim.nf'
include { sim as sim_seed_4_gen_7_cell_0 } from './sim.nf'
include { sim as sim_seed_4_gen_8_cell_0 } from './sim.nf'
include { sim as sim_seed_4_gen_9_cell_0 } from './sim.nf'
include { sim_gen_0 as sim_seed_5_gen_0_cell_0 } from './sim.nf'
include { sim as sim_seed_5_gen_1_cell_0 } from './sim.nf'
include { sim as sim_seed_5_gen_2_cell_0 } from './sim.nf'
include { sim as sim_seed_5_gen_3_cell_0 } from './sim.nf'
include { sim as sim_seed_5_gen_4_cell_0 } from './sim.nf'
include { sim as sim_seed_5_gen_5_cell_0 } from './sim.nf'
include { sim as sim_seed_5_gen_6_cell_0 } from './sim.nf'
include { sim as sim_seed_5_gen_7_cell_0 } from './sim.nf'
include { sim as sim_seed_5_gen_8_cell_0 } from './sim.nf'
include { sim as sim_seed_5_gen_9_cell_0 } from './sim.nf'
include { sim_gen_0 as sim_seed_6_gen_0_cell_0 } from './sim.nf'
include { sim as sim_seed_6_gen_1_cell_0 } from './sim.nf'
include { sim as sim_seed_6_gen_2_cell_0 } from './sim.nf'
include { sim as sim_seed_6_gen_3_cell_0 } from './sim.nf'
include { sim as sim_seed_6_gen_4_cell_0 } from './sim.nf'
include { sim as sim_seed_6_gen_5_cell_0 } from './sim.nf'
include { sim as sim_seed_6_gen_6_cell_0 } from './sim.nf'
include { sim as sim_seed_6_gen_7_cell_0 } from './sim.nf'
include { sim as sim_seed_6_gen_8_cell_0 } from './sim.nf'
include { sim as sim_seed_6_gen_9_cell_0 } from './sim.nf'
include { sim_gen_0 as sim_seed_7_gen_0_cell_0 } from './sim.nf'
include { sim as sim_seed_7_gen_1_cell_0 } from './sim.nf'
include { sim as sim_seed_7_gen_2_cell_0 } from './sim.nf'
include { sim as sim_seed_7_gen_3_cell_0 } from './sim.nf'
include { sim as sim_seed_7_gen_4_cell_0 } from './sim.nf'
include { sim as sim_seed_7_gen_5_cell_0 } from './sim.nf'
include { sim as sim_seed_7_gen_6_cell_0 } from './sim.nf'
include { sim as sim_seed_7_gen_7_cell_0 } from './sim.nf'
include { sim as sim_seed_7_gen_8_cell_0 } from './sim.nf'
include { sim as sim_seed_7_gen_9_cell_0 } from './sim.nf'
include { sim_gen_0 as sim_seed_8_gen_0_cell_0 } from './sim.nf'
include { sim as sim_seed_8_gen_1_cell_0 } from './sim.nf'
include { sim as sim_seed_8_gen_2_cell_0 } from './sim.nf'
include { sim as sim_seed_8_gen_3_cell_0 } from './sim.nf'
include { sim as sim_seed_8_gen_4_cell_0 } from './sim.nf'
include { sim as sim_seed_8_gen_5_cell_0 } from './sim.nf'
include { sim as sim_seed_8_gen_6_cell_0 } from './sim.nf'
include { sim as sim_seed_8_gen_7_cell_0 } from './sim.nf'
include { sim as sim_seed_8_gen_8_cell_0 } from './sim.nf'
include { sim as sim_seed_8_gen_9_cell_0 } from './sim.nf'
include { sim_gen_0 as sim_seed_9_gen_0_cell_0 } from './sim.nf'
include { sim as sim_seed_9_gen_1_cell_0 } from './sim.nf'
include { sim as sim_seed_9_gen_2_cell_0 } from './sim.nf'
include { sim as sim_seed_9_gen_3_cell_0 } from './sim.nf'
include { sim as sim_seed_9_gen_4_cell_0 } from './sim.nf'
include { sim as sim_seed_9_gen_5_cell_0 } from './sim.nf'
include { sim as sim_seed_9_gen_6_cell_0 } from './sim.nf'
include { sim as sim_seed_9_gen_7_cell_0 } from './sim.nf'
include { sim as sim_seed_9_gen_8_cell_0 } from './sim.nf'
include { sim as sim_seed_9_gen_9_cell_0 } from './sim.nf'
include { analysis_variant } from './analysis.nf'
include { analysis_cohort } from './analysis.nf'
include { analysis_multigen } from './analysis.nf'
include { analysis_single } from './analysis.nf'

workflow {
    println workflow.sessionId
    println workflow.start
    // run_parca(params.config)
    // run_parca.out.toList().set { kb }
    // create_variants(params.config, kb)
    //     .variant_sim_data
    //     .flatten()
    //     .set { variant_ch }
	// sim_seed_0_gen_0_cell_0(params.config, variant_ch.combine([0]).combine([0]), 0, 0)
	// sim_seed_0_gen_1_cell_0(sim_seed_0_gen_0_cell_0.out.config, sim_seed_0_gen_0_cell_0.out.next_gen, sim_seed_0_gen_0_cell_0.out.d1, 1, 0)
	// sim_seed_0_gen_2_cell_0(sim_seed_0_gen_1_cell_0.out.config, sim_seed_0_gen_1_cell_0.out.next_gen, sim_seed_0_gen_1_cell_0.out.d1, 2, 0)
	// sim_seed_0_gen_3_cell_0(sim_seed_0_gen_2_cell_0.out.config, sim_seed_0_gen_2_cell_0.out.next_gen, sim_seed_0_gen_2_cell_0.out.d1, 3, 0)
	// sim_seed_0_gen_4_cell_0(sim_seed_0_gen_3_cell_0.out.config, sim_seed_0_gen_3_cell_0.out.next_gen, sim_seed_0_gen_3_cell_0.out.d1, 4, 0)
	// sim_seed_0_gen_5_cell_0(sim_seed_0_gen_4_cell_0.out.config, sim_seed_0_gen_4_cell_0.out.next_gen, sim_seed_0_gen_4_cell_0.out.d1, 5, 0)
	// sim_seed_0_gen_6_cell_0(sim_seed_0_gen_5_cell_0.out.config, sim_seed_0_gen_5_cell_0.out.next_gen, sim_seed_0_gen_5_cell_0.out.d1, 6, 0)
	// sim_seed_0_gen_7_cell_0(sim_seed_0_gen_6_cell_0.out.config, sim_seed_0_gen_6_cell_0.out.next_gen, sim_seed_0_gen_6_cell_0.out.d1, 7, 0)
	// sim_seed_0_gen_8_cell_0(sim_seed_0_gen_7_cell_0.out.config, sim_seed_0_gen_7_cell_0.out.next_gen, sim_seed_0_gen_7_cell_0.out.d1, 8, 0)
	// sim_seed_0_gen_9_cell_0(sim_seed_0_gen_8_cell_0.out.config, sim_seed_0_gen_8_cell_0.out.next_gen, sim_seed_0_gen_8_cell_0.out.d1, 9, 0)
	// sim_seed_1_gen_0_cell_0(params.config, variant_ch.combine([1]).combine([0]), 1, 0)
	// sim_seed_1_gen_1_cell_0(sim_seed_1_gen_0_cell_0.out.config, sim_seed_1_gen_0_cell_0.out.next_gen, sim_seed_1_gen_0_cell_0.out.d1, 2, 0)
	// sim_seed_1_gen_2_cell_0(sim_seed_1_gen_1_cell_0.out.config, sim_seed_1_gen_1_cell_0.out.next_gen, sim_seed_1_gen_1_cell_0.out.d1, 3, 0)
	// sim_seed_1_gen_3_cell_0(sim_seed_1_gen_2_cell_0.out.config, sim_seed_1_gen_2_cell_0.out.next_gen, sim_seed_1_gen_2_cell_0.out.d1, 4, 0)
	// sim_seed_1_gen_4_cell_0(sim_seed_1_gen_3_cell_0.out.config, sim_seed_1_gen_3_cell_0.out.next_gen, sim_seed_1_gen_3_cell_0.out.d1, 5, 0)
	// sim_seed_1_gen_5_cell_0(sim_seed_1_gen_4_cell_0.out.config, sim_seed_1_gen_4_cell_0.out.next_gen, sim_seed_1_gen_4_cell_0.out.d1, 6, 0)
	// sim_seed_1_gen_6_cell_0(sim_seed_1_gen_5_cell_0.out.config, sim_seed_1_gen_5_cell_0.out.next_gen, sim_seed_1_gen_5_cell_0.out.d1, 7, 0)
	// sim_seed_1_gen_7_cell_0(sim_seed_1_gen_6_cell_0.out.config, sim_seed_1_gen_6_cell_0.out.next_gen, sim_seed_1_gen_6_cell_0.out.d1, 8, 0)
	// sim_seed_1_gen_8_cell_0(sim_seed_1_gen_7_cell_0.out.config, sim_seed_1_gen_7_cell_0.out.next_gen, sim_seed_1_gen_7_cell_0.out.d1, 9, 0)
	// sim_seed_1_gen_9_cell_0(sim_seed_1_gen_8_cell_0.out.config, sim_seed_1_gen_8_cell_0.out.next_gen, sim_seed_1_gen_8_cell_0.out.d1, 10, 0)
	// sim_seed_2_gen_0_cell_0(params.config, variant_ch.combine([2]).combine([0]), 2, 0)
	// sim_seed_2_gen_1_cell_0(sim_seed_2_gen_0_cell_0.out.config, sim_seed_2_gen_0_cell_0.out.next_gen, sim_seed_2_gen_0_cell_0.out.d1, 3, 0)
	// sim_seed_2_gen_2_cell_0(sim_seed_2_gen_1_cell_0.out.config, sim_seed_2_gen_1_cell_0.out.next_gen, sim_seed_2_gen_1_cell_0.out.d1, 4, 0)
	// sim_seed_2_gen_3_cell_0(sim_seed_2_gen_2_cell_0.out.config, sim_seed_2_gen_2_cell_0.out.next_gen, sim_seed_2_gen_2_cell_0.out.d1, 5, 0)
	// sim_seed_2_gen_4_cell_0(sim_seed_2_gen_3_cell_0.out.config, sim_seed_2_gen_3_cell_0.out.next_gen, sim_seed_2_gen_3_cell_0.out.d1, 6, 0)
	// sim_seed_2_gen_5_cell_0(sim_seed_2_gen_4_cell_0.out.config, sim_seed_2_gen_4_cell_0.out.next_gen, sim_seed_2_gen_4_cell_0.out.d1, 7, 0)
	// sim_seed_2_gen_6_cell_0(sim_seed_2_gen_5_cell_0.out.config, sim_seed_2_gen_5_cell_0.out.next_gen, sim_seed_2_gen_5_cell_0.out.d1, 8, 0)
	// sim_seed_2_gen_7_cell_0(sim_seed_2_gen_6_cell_0.out.config, sim_seed_2_gen_6_cell_0.out.next_gen, sim_seed_2_gen_6_cell_0.out.d1, 9, 0)
	// sim_seed_2_gen_8_cell_0(sim_seed_2_gen_7_cell_0.out.config, sim_seed_2_gen_7_cell_0.out.next_gen, sim_seed_2_gen_7_cell_0.out.d1, 10, 0)
	// sim_seed_2_gen_9_cell_0(sim_seed_2_gen_8_cell_0.out.config, sim_seed_2_gen_8_cell_0.out.next_gen, sim_seed_2_gen_8_cell_0.out.d1, 11, 0)
	// sim_seed_3_gen_0_cell_0(params.config, variant_ch.combine([3]).combine([0]), 3, 0)
	// sim_seed_3_gen_1_cell_0(sim_seed_3_gen_0_cell_0.out.config, sim_seed_3_gen_0_cell_0.out.next_gen, sim_seed_3_gen_0_cell_0.out.d1, 4, 0)
	// sim_seed_3_gen_2_cell_0(sim_seed_3_gen_1_cell_0.out.config, sim_seed_3_gen_1_cell_0.out.next_gen, sim_seed_3_gen_1_cell_0.out.d1, 5, 0)
	// sim_seed_3_gen_3_cell_0(sim_seed_3_gen_2_cell_0.out.config, sim_seed_3_gen_2_cell_0.out.next_gen, sim_seed_3_gen_2_cell_0.out.d1, 6, 0)
	// sim_seed_3_gen_4_cell_0(sim_seed_3_gen_3_cell_0.out.config, sim_seed_3_gen_3_cell_0.out.next_gen, sim_seed_3_gen_3_cell_0.out.d1, 7, 0)
	// sim_seed_3_gen_5_cell_0(sim_seed_3_gen_4_cell_0.out.config, sim_seed_3_gen_4_cell_0.out.next_gen, sim_seed_3_gen_4_cell_0.out.d1, 8, 0)
	// sim_seed_3_gen_6_cell_0(sim_seed_3_gen_5_cell_0.out.config, sim_seed_3_gen_5_cell_0.out.next_gen, sim_seed_3_gen_5_cell_0.out.d1, 9, 0)
	// sim_seed_3_gen_7_cell_0(sim_seed_3_gen_6_cell_0.out.config, sim_seed_3_gen_6_cell_0.out.next_gen, sim_seed_3_gen_6_cell_0.out.d1, 10, 0)
	// sim_seed_3_gen_8_cell_0(sim_seed_3_gen_7_cell_0.out.config, sim_seed_3_gen_7_cell_0.out.next_gen, sim_seed_3_gen_7_cell_0.out.d1, 11, 0)
	// sim_seed_3_gen_9_cell_0(sim_seed_3_gen_8_cell_0.out.config, sim_seed_3_gen_8_cell_0.out.next_gen, sim_seed_3_gen_8_cell_0.out.d1, 12, 0)
	// sim_seed_4_gen_0_cell_0(params.config, variant_ch.combine([4]).combine([0]), 4, 0)
	// sim_seed_4_gen_1_cell_0(sim_seed_4_gen_0_cell_0.out.config, sim_seed_4_gen_0_cell_0.out.next_gen, sim_seed_4_gen_0_cell_0.out.d1, 5, 0)
	// sim_seed_4_gen_2_cell_0(sim_seed_4_gen_1_cell_0.out.config, sim_seed_4_gen_1_cell_0.out.next_gen, sim_seed_4_gen_1_cell_0.out.d1, 6, 0)
	// sim_seed_4_gen_3_cell_0(sim_seed_4_gen_2_cell_0.out.config, sim_seed_4_gen_2_cell_0.out.next_gen, sim_seed_4_gen_2_cell_0.out.d1, 7, 0)
	// sim_seed_4_gen_4_cell_0(sim_seed_4_gen_3_cell_0.out.config, sim_seed_4_gen_3_cell_0.out.next_gen, sim_seed_4_gen_3_cell_0.out.d1, 8, 0)
	// sim_seed_4_gen_5_cell_0(sim_seed_4_gen_4_cell_0.out.config, sim_seed_4_gen_4_cell_0.out.next_gen, sim_seed_4_gen_4_cell_0.out.d1, 9, 0)
	// sim_seed_4_gen_6_cell_0(sim_seed_4_gen_5_cell_0.out.config, sim_seed_4_gen_5_cell_0.out.next_gen, sim_seed_4_gen_5_cell_0.out.d1, 10, 0)
	// sim_seed_4_gen_7_cell_0(sim_seed_4_gen_6_cell_0.out.config, sim_seed_4_gen_6_cell_0.out.next_gen, sim_seed_4_gen_6_cell_0.out.d1, 11, 0)
	// sim_seed_4_gen_8_cell_0(sim_seed_4_gen_7_cell_0.out.config, sim_seed_4_gen_7_cell_0.out.next_gen, sim_seed_4_gen_7_cell_0.out.d1, 12, 0)
	// sim_seed_4_gen_9_cell_0(sim_seed_4_gen_8_cell_0.out.config, sim_seed_4_gen_8_cell_0.out.next_gen, sim_seed_4_gen_8_cell_0.out.d1, 13, 0)
	// sim_seed_5_gen_0_cell_0(params.config, variant_ch.combine([5]).combine([0]), 5, 0)
	// sim_seed_5_gen_1_cell_0(sim_seed_5_gen_0_cell_0.out.config, sim_seed_5_gen_0_cell_0.out.next_gen, sim_seed_5_gen_0_cell_0.out.d1, 6, 0)
	// sim_seed_5_gen_2_cell_0(sim_seed_5_gen_1_cell_0.out.config, sim_seed_5_gen_1_cell_0.out.next_gen, sim_seed_5_gen_1_cell_0.out.d1, 7, 0)
	// sim_seed_5_gen_3_cell_0(sim_seed_5_gen_2_cell_0.out.config, sim_seed_5_gen_2_cell_0.out.next_gen, sim_seed_5_gen_2_cell_0.out.d1, 8, 0)
	// sim_seed_5_gen_4_cell_0(sim_seed_5_gen_3_cell_0.out.config, sim_seed_5_gen_3_cell_0.out.next_gen, sim_seed_5_gen_3_cell_0.out.d1, 9, 0)
	// sim_seed_5_gen_5_cell_0(sim_seed_5_gen_4_cell_0.out.config, sim_seed_5_gen_4_cell_0.out.next_gen, sim_seed_5_gen_4_cell_0.out.d1, 10, 0)
	// sim_seed_5_gen_6_cell_0(sim_seed_5_gen_5_cell_0.out.config, sim_seed_5_gen_5_cell_0.out.next_gen, sim_seed_5_gen_5_cell_0.out.d1, 11, 0)
	// sim_seed_5_gen_7_cell_0(sim_seed_5_gen_6_cell_0.out.config, sim_seed_5_gen_6_cell_0.out.next_gen, sim_seed_5_gen_6_cell_0.out.d1, 12, 0)
	// sim_seed_5_gen_8_cell_0(sim_seed_5_gen_7_cell_0.out.config, sim_seed_5_gen_7_cell_0.out.next_gen, sim_seed_5_gen_7_cell_0.out.d1, 13, 0)
	// sim_seed_5_gen_9_cell_0(sim_seed_5_gen_8_cell_0.out.config, sim_seed_5_gen_8_cell_0.out.next_gen, sim_seed_5_gen_8_cell_0.out.d1, 14, 0)
	// sim_seed_6_gen_0_cell_0(params.config, variant_ch.combine([6]).combine([0]), 6, 0)
	// sim_seed_6_gen_1_cell_0(sim_seed_6_gen_0_cell_0.out.config, sim_seed_6_gen_0_cell_0.out.next_gen, sim_seed_6_gen_0_cell_0.out.d1, 7, 0)
	// sim_seed_6_gen_2_cell_0(sim_seed_6_gen_1_cell_0.out.config, sim_seed_6_gen_1_cell_0.out.next_gen, sim_seed_6_gen_1_cell_0.out.d1, 8, 0)
	// sim_seed_6_gen_3_cell_0(sim_seed_6_gen_2_cell_0.out.config, sim_seed_6_gen_2_cell_0.out.next_gen, sim_seed_6_gen_2_cell_0.out.d1, 9, 0)
	// sim_seed_6_gen_4_cell_0(sim_seed_6_gen_3_cell_0.out.config, sim_seed_6_gen_3_cell_0.out.next_gen, sim_seed_6_gen_3_cell_0.out.d1, 10, 0)
	// sim_seed_6_gen_5_cell_0(sim_seed_6_gen_4_cell_0.out.config, sim_seed_6_gen_4_cell_0.out.next_gen, sim_seed_6_gen_4_cell_0.out.d1, 11, 0)
	// sim_seed_6_gen_6_cell_0(sim_seed_6_gen_5_cell_0.out.config, sim_seed_6_gen_5_cell_0.out.next_gen, sim_seed_6_gen_5_cell_0.out.d1, 12, 0)
	// sim_seed_6_gen_7_cell_0(sim_seed_6_gen_6_cell_0.out.config, sim_seed_6_gen_6_cell_0.out.next_gen, sim_seed_6_gen_6_cell_0.out.d1, 13, 0)
	// sim_seed_6_gen_8_cell_0(sim_seed_6_gen_7_cell_0.out.config, sim_seed_6_gen_7_cell_0.out.next_gen, sim_seed_6_gen_7_cell_0.out.d1, 14, 0)
	// sim_seed_6_gen_9_cell_0(sim_seed_6_gen_8_cell_0.out.config, sim_seed_6_gen_8_cell_0.out.next_gen, sim_seed_6_gen_8_cell_0.out.d1, 15, 0)
	// sim_seed_7_gen_0_cell_0(params.config, variant_ch.combine([7]).combine([0]), 7, 0)
	// sim_seed_7_gen_1_cell_0(sim_seed_7_gen_0_cell_0.out.config, sim_seed_7_gen_0_cell_0.out.next_gen, sim_seed_7_gen_0_cell_0.out.d1, 8, 0)
	// sim_seed_7_gen_2_cell_0(sim_seed_7_gen_1_cell_0.out.config, sim_seed_7_gen_1_cell_0.out.next_gen, sim_seed_7_gen_1_cell_0.out.d1, 9, 0)
	// sim_seed_7_gen_3_cell_0(sim_seed_7_gen_2_cell_0.out.config, sim_seed_7_gen_2_cell_0.out.next_gen, sim_seed_7_gen_2_cell_0.out.d1, 10, 0)
	// sim_seed_7_gen_4_cell_0(sim_seed_7_gen_3_cell_0.out.config, sim_seed_7_gen_3_cell_0.out.next_gen, sim_seed_7_gen_3_cell_0.out.d1, 11, 0)
	// sim_seed_7_gen_5_cell_0(sim_seed_7_gen_4_cell_0.out.config, sim_seed_7_gen_4_cell_0.out.next_gen, sim_seed_7_gen_4_cell_0.out.d1, 12, 0)
	// sim_seed_7_gen_6_cell_0(sim_seed_7_gen_5_cell_0.out.config, sim_seed_7_gen_5_cell_0.out.next_gen, sim_seed_7_gen_5_cell_0.out.d1, 13, 0)
	// sim_seed_7_gen_7_cell_0(sim_seed_7_gen_6_cell_0.out.config, sim_seed_7_gen_6_cell_0.out.next_gen, sim_seed_7_gen_6_cell_0.out.d1, 14, 0)
	// sim_seed_7_gen_8_cell_0(sim_seed_7_gen_7_cell_0.out.config, sim_seed_7_gen_7_cell_0.out.next_gen, sim_seed_7_gen_7_cell_0.out.d1, 15, 0)
	// sim_seed_7_gen_9_cell_0(sim_seed_7_gen_8_cell_0.out.config, sim_seed_7_gen_8_cell_0.out.next_gen, sim_seed_7_gen_8_cell_0.out.d1, 16, 0)
	// sim_seed_8_gen_0_cell_0(params.config, variant_ch.combine([8]).combine([0]), 8, 0)
	// sim_seed_8_gen_1_cell_0(sim_seed_8_gen_0_cell_0.out.config, sim_seed_8_gen_0_cell_0.out.next_gen, sim_seed_8_gen_0_cell_0.out.d1, 9, 0)
	// sim_seed_8_gen_2_cell_0(sim_seed_8_gen_1_cell_0.out.config, sim_seed_8_gen_1_cell_0.out.next_gen, sim_seed_8_gen_1_cell_0.out.d1, 10, 0)
	// sim_seed_8_gen_3_cell_0(sim_seed_8_gen_2_cell_0.out.config, sim_seed_8_gen_2_cell_0.out.next_gen, sim_seed_8_gen_2_cell_0.out.d1, 11, 0)
	// sim_seed_8_gen_4_cell_0(sim_seed_8_gen_3_cell_0.out.config, sim_seed_8_gen_3_cell_0.out.next_gen, sim_seed_8_gen_3_cell_0.out.d1, 12, 0)
	// sim_seed_8_gen_5_cell_0(sim_seed_8_gen_4_cell_0.out.config, sim_seed_8_gen_4_cell_0.out.next_gen, sim_seed_8_gen_4_cell_0.out.d1, 13, 0)
	// sim_seed_8_gen_6_cell_0(sim_seed_8_gen_5_cell_0.out.config, sim_seed_8_gen_5_cell_0.out.next_gen, sim_seed_8_gen_5_cell_0.out.d1, 14, 0)
	// sim_seed_8_gen_7_cell_0(sim_seed_8_gen_6_cell_0.out.config, sim_seed_8_gen_6_cell_0.out.next_gen, sim_seed_8_gen_6_cell_0.out.d1, 15, 0)
	// sim_seed_8_gen_8_cell_0(sim_seed_8_gen_7_cell_0.out.config, sim_seed_8_gen_7_cell_0.out.next_gen, sim_seed_8_gen_7_cell_0.out.d1, 16, 0)
	// sim_seed_8_gen_9_cell_0(sim_seed_8_gen_8_cell_0.out.config, sim_seed_8_gen_8_cell_0.out.next_gen, sim_seed_8_gen_8_cell_0.out.d1, 17, 0)
	// sim_seed_9_gen_0_cell_0(params.config, variant_ch.combine([9]).combine([0]), 9, 0)
	// sim_seed_9_gen_1_cell_0(sim_seed_9_gen_0_cell_0.out.config, sim_seed_9_gen_0_cell_0.out.next_gen, sim_seed_9_gen_0_cell_0.out.d1, 10, 0)
	// sim_seed_9_gen_2_cell_0(sim_seed_9_gen_1_cell_0.out.config, sim_seed_9_gen_1_cell_0.out.next_gen, sim_seed_9_gen_1_cell_0.out.d1, 11, 0)
	// sim_seed_9_gen_3_cell_0(sim_seed_9_gen_2_cell_0.out.config, sim_seed_9_gen_2_cell_0.out.next_gen, sim_seed_9_gen_2_cell_0.out.d1, 12, 0)
	// sim_seed_9_gen_4_cell_0(sim_seed_9_gen_3_cell_0.out.config, sim_seed_9_gen_3_cell_0.out.next_gen, sim_seed_9_gen_3_cell_0.out.d1, 13, 0)
	// sim_seed_9_gen_5_cell_0(sim_seed_9_gen_4_cell_0.out.config, sim_seed_9_gen_4_cell_0.out.next_gen, sim_seed_9_gen_4_cell_0.out.d1, 14, 0)
	// sim_seed_9_gen_6_cell_0(sim_seed_9_gen_5_cell_0.out.config, sim_seed_9_gen_5_cell_0.out.next_gen, sim_seed_9_gen_5_cell_0.out.d1, 15, 0)
	// sim_seed_9_gen_7_cell_0(sim_seed_9_gen_6_cell_0.out.config, sim_seed_9_gen_6_cell_0.out.next_gen, sim_seed_9_gen_6_cell_0.out.d1, 16, 0)
	// sim_seed_9_gen_8_cell_0(sim_seed_9_gen_7_cell_0.out.config, sim_seed_9_gen_7_cell_0.out.next_gen, sim_seed_9_gen_7_cell_0.out.d1, 17, 0)
	// sim_seed_9_gen_9_cell_0(sim_seed_9_gen_8_cell_0.out.config, sim_seed_9_gen_8_cell_0.out.next_gen, sim_seed_9_gen_8_cell_0.out.d1, 18, 0)

    // sim_seed_0_gen_0_cell_0.out.metadata
    //     .mix(sim_seed_0_gen_1_cell_0.out.metadata, sim_seed_0_gen_2_cell_0.out.metadata, sim_seed_0_gen_3_cell_0.out.metadata, sim_seed_0_gen_4_cell_0.out.metadata, sim_seed_0_gen_5_cell_0.out.metadata, sim_seed_0_gen_6_cell_0.out.metadata, sim_seed_0_gen_7_cell_0.out.metadata, sim_seed_0_gen_8_cell_0.out.metadata, sim_seed_0_gen_9_cell_0.out.metadata, sim_seed_1_gen_0_cell_0.out.metadata, sim_seed_1_gen_1_cell_0.out.metadata, sim_seed_1_gen_2_cell_0.out.metadata, sim_seed_1_gen_3_cell_0.out.metadata, sim_seed_1_gen_4_cell_0.out.metadata, sim_seed_1_gen_5_cell_0.out.metadata, sim_seed_1_gen_6_cell_0.out.metadata, sim_seed_1_gen_7_cell_0.out.metadata, sim_seed_1_gen_8_cell_0.out.metadata, sim_seed_1_gen_9_cell_0.out.metadata, sim_seed_2_gen_0_cell_0.out.metadata, sim_seed_2_gen_1_cell_0.out.metadata, sim_seed_2_gen_2_cell_0.out.metadata, sim_seed_2_gen_3_cell_0.out.metadata, sim_seed_2_gen_4_cell_0.out.metadata, sim_seed_2_gen_5_cell_0.out.metadata, sim_seed_2_gen_6_cell_0.out.metadata, sim_seed_2_gen_7_cell_0.out.metadata, sim_seed_2_gen_8_cell_0.out.metadata, sim_seed_2_gen_9_cell_0.out.metadata, sim_seed_3_gen_0_cell_0.out.metadata, sim_seed_3_gen_1_cell_0.out.metadata, sim_seed_3_gen_2_cell_0.out.metadata, sim_seed_3_gen_3_cell_0.out.metadata, sim_seed_3_gen_4_cell_0.out.metadata, sim_seed_3_gen_5_cell_0.out.metadata, sim_seed_3_gen_6_cell_0.out.metadata, sim_seed_3_gen_7_cell_0.out.metadata, sim_seed_3_gen_8_cell_0.out.metadata, sim_seed_3_gen_9_cell_0.out.metadata, sim_seed_4_gen_0_cell_0.out.metadata, sim_seed_4_gen_1_cell_0.out.metadata, sim_seed_4_gen_2_cell_0.out.metadata, sim_seed_4_gen_3_cell_0.out.metadata, sim_seed_4_gen_4_cell_0.out.metadata, sim_seed_4_gen_5_cell_0.out.metadata, sim_seed_4_gen_6_cell_0.out.metadata, sim_seed_4_gen_7_cell_0.out.metadata, sim_seed_4_gen_8_cell_0.out.metadata, sim_seed_4_gen_9_cell_0.out.metadata, sim_seed_5_gen_0_cell_0.out.metadata, sim_seed_5_gen_1_cell_0.out.metadata, sim_seed_5_gen_2_cell_0.out.metadata, sim_seed_5_gen_3_cell_0.out.metadata, sim_seed_5_gen_4_cell_0.out.metadata, sim_seed_5_gen_5_cell_0.out.metadata, sim_seed_5_gen_6_cell_0.out.metadata, sim_seed_5_gen_7_cell_0.out.metadata, sim_seed_5_gen_8_cell_0.out.metadata, sim_seed_5_gen_9_cell_0.out.metadata, sim_seed_6_gen_0_cell_0.out.metadata, sim_seed_6_gen_1_cell_0.out.metadata, sim_seed_6_gen_2_cell_0.out.metadata, sim_seed_6_gen_3_cell_0.out.metadata, sim_seed_6_gen_4_cell_0.out.metadata, sim_seed_6_gen_5_cell_0.out.metadata, sim_seed_6_gen_6_cell_0.out.metadata, sim_seed_6_gen_7_cell_0.out.metadata, sim_seed_6_gen_8_cell_0.out.metadata, sim_seed_6_gen_9_cell_0.out.metadata, sim_seed_7_gen_0_cell_0.out.metadata, sim_seed_7_gen_1_cell_0.out.metadata, sim_seed_7_gen_2_cell_0.out.metadata, sim_seed_7_gen_3_cell_0.out.metadata, sim_seed_7_gen_4_cell_0.out.metadata, sim_seed_7_gen_5_cell_0.out.metadata, sim_seed_7_gen_6_cell_0.out.metadata, sim_seed_7_gen_7_cell_0.out.metadata, sim_seed_7_gen_8_cell_0.out.metadata, sim_seed_7_gen_9_cell_0.out.metadata, sim_seed_8_gen_0_cell_0.out.metadata, sim_seed_8_gen_1_cell_0.out.metadata, sim_seed_8_gen_2_cell_0.out.metadata, sim_seed_8_gen_3_cell_0.out.metadata, sim_seed_8_gen_4_cell_0.out.metadata, sim_seed_8_gen_5_cell_0.out.metadata, sim_seed_8_gen_6_cell_0.out.metadata, sim_seed_8_gen_7_cell_0.out.metadata, sim_seed_8_gen_8_cell_0.out.metadata, sim_seed_8_gen_9_cell_0.out.metadata, sim_seed_9_gen_0_cell_0.out.metadata, sim_seed_9_gen_1_cell_0.out.metadata, sim_seed_9_gen_2_cell_0.out.metadata, sim_seed_9_gen_3_cell_0.out.metadata, sim_seed_9_gen_4_cell_0.out.metadata, sim_seed_9_gen_5_cell_0.out.metadata, sim_seed_9_gen_6_cell_0.out.metadata, sim_seed_9_gen_7_cell_0.out.metadata, sim_seed_9_gen_8_cell_0.out.metadata, sim_seed_9_gen_9_cell_0.out.metadata)
    //     .set { sim_ch }


    // sim_ch
    //     .groupTuple(by: [5])
    //     .set { variant_ch }

	// analysis_variant(params.config, kb, variant_ch)

    // sim_ch
    //     .groupTuple(by: 1, size: 100)
    //     .set { cohort_ch }

	// analysis_cohort(params.config, kb, cohort_ch)

    // sim_ch
    //     .groupTuple(by: [1, 2], size: 10)
    //     .set { multigen_ch }

	// analysis_multigen(params.config, kb, multigen_ch)
	// analysis_single(params.config, kb, sim_ch)
	// analysis_parca(params.config, kb)
}
