process run_parca {
    // Run ParCa using parca_options from config JSON
    publishDir "${params.publishDir}/parca"

    input:
    path config

    output:
    path 'kb'

    script:
    """
    python ${params.projectRoot}/scripts/run_parca.py -c $config -o \$(pwd)
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

process analysis_parca {
    publishDir "${params.publishDir}/parca"

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

process create_variants {
    // Parse variants in config JSON to generate variants
    publishDir "${params.publishDir}/variant_sim_data"

    input:
    path config
    path kb

    output:
    path '*.cPickle', emit: variant_sim_data
    path 'metadata.json', emit: variant_metadata

    script:
    """
    python ${params.projectRoot}/scripts/create_variants.py \
        -c $config --kb $kb -o \$(pwd)
    cp $kb/simData.cPickle baseline.cPickle
    """

    stub:
    """
    cp $kb/simData.cPickle variant_1.cPickle
    echo "Mock variant 1" >> variant_1.cPickle
    cp $kb/simData.cPickle variant_2.cPickle
    echo "Mock variant 2" >> variant_2.cPickle
    echo "Mock metadata.json" > metadata.json
    cp $kb/simData.cPickle baseline.cPickle
    """
}

process analysis_single {
    publishDir "${params.publishDir}/analyses/${variant}/${seed}/${generation}/${cell_id}"

    input:
    path config
    path kb
    tuple path(sim_data, stageAs: 'variant_sim_data_*.cPickle'), val(variant), val(seed), val(generation), val(cell_id), val(workflow_id)

    output:
    path 'plots/*'

    script:
    """
    python ${params.projectRoot}/scripts/analysis.py -c $config \
        --sim-data-path=$sim_data \
        --validation-data-path=$kb/validationData.cPickle \
        --single -o \$(pwd)
    """

    stub:
    """
    mkdir plots
    echo -e "$sim_data\n\n$seed\n\n$generation\n\n$cell_id\
        \n\n$kb\n\n$config" > plots/test.txt
    """
}

process analysis_multigen {
    publishDir "${params.publishDir}/analyses/${variant}/${seed}"

    input:
    path config
    path kb
    tuple path(sim_data, stageAs: 'variant_sim_data_*.cPickle'), val(variant), val(seed), val(generation), val(cell_id), val(workflow_id)

    output:
    path 'plots/*'

    script:
    """
    python ${params.projectRoot}/scripts/analysis.py -c $config \
        --sim-data-path=$sim_data \
        --validation-data-path=$kb/validationData.cPickle \
        --multigen -o \$(pwd)
    """

    stub:
    """
    mkdir plots
    echo -e "$sim_data\n\n$seed\n\n$generation\n\n$cell_id\
        \n\n$kb\n\n$config" > plots/test.txt
    """
}

process analysis_cohort {
    publishDir "${params.publishDir}/analyses/${variant_name}"

    input:
    path config
    path kb
    tuple path(sim_data, stageAs: 'variant_sim_data_*.cPickle'), val(variant_name), val(seed), val(generation), val(cell_id), val(workflow_id)

    output:
    path 'plots/*'

    script:
    """
    python ${params.projectRoot}/scripts/analysis.py -c $config \
        --sim-data-path=$sim_data \
        --validation-data-path=$kb/validationData.cPickle \
        --cohort -o \$(pwd)
    """

    stub:
    """
    mkdir plots
    echo -e "$sim_data\n\n$seed\n\n$generation\n\n$cell_id\
        \n\n$kb\n\n$config" > plots/test.txt
    """
}

process analysis_variant {
    publishDir "${params.publishDir}/analyses"

    input:
    path config
    path kb
    tuple path(sim_data, stageAs: 'variant_sim_data_*.cPickle'), val(variant_name), val(seed), val(generation), val(cell_id), val(workflow_id)

    output:
    path 'plots/*'

    script:
    """
    python ${params.projectRoot}/scripts/analysis.py -c $config \
        --sim-data-path=$sim_data \
        --validation-data-path=$kb/validationData.cPickle \
        --variant -o \$(pwd)
    """

    stub:
    """
    mkdir plots
    echo -e "$sim_data\n\n$seed\n\n$generation\n\n$cell_id\
        \n\n$kb\n\n$config" > plots/test.txt
    """
}

process colony {
    input:
    path config
    path sim_data
    path initial_state

    output:
    env STATUS

    script:
    """
    python /vivarium-ecoli/ecoli/experiments/ecoli_engine_process.py -c $config --sim_data_path $sim_data --initial_state_file $initial_state
    STATUS=\$?
    """

    stub:
    """
    STATUS=0
    """
}

process sim_gen_0 {
    errorStrategy { (task.attempt <= process.maxRetries) ? 'retry' : 'ignore' }

    tag "${sim_data.getBaseName()}"

    input:
    path config
    tuple path(sim_data), val(initial_seed), val(generation)
    val sim_seed
    val cell_id

    output:
    path config, emit: config
    tuple path(sim_data), val(initial_seed), val(generation), emit: next_gen
    path 'daughter_state_0.json', emit: d1
    path 'daughter_state_1.json', emit: d2
    tuple path(sim_data), val("${sim_data.getBaseName()}"), val(initial_seed), val(generation), val(cell_id), val(0), emit: metadata

    script:
    """
    # Create daughter states so workflow can continue
    touch daughter_state_0.json
    touch daughter_state_1.json
    python ${params.project_root}/ecoli/experiments/ecoli_master_sim.py \
        -c $config \
        --sim_data_path $sim_data \
        --daughter_outdir \$(pwd)
    """

    stub:
    """
    echo "$config $sim_data $initial_seed $generation" > daughter_state_0.json
    echo "$sim_seed" > daughter_state_1.json
    """
}

process sim {
    errorStrategy { (task.attempt <= process.maxRetries) ? 'retry' : 'ignore' }

    tag "${sim_data.getBaseName()}"

    input:
    path config
    tuple path(sim_data), val(initial_seed), val(generation)
    path initial_state
    val sim_seed
    val cell_id

    output:
    path config, emit: config
    tuple path(sim_data), val(initial_seed), val(generation), emit: next_gen
    path 'daughter_state_0.json', emit: d1
    path 'daughter_state_1.json', emit: d2
    tuple path(sim_data), val("${sim_data.getBaseName()}"), val(initial_seed), val(generation), val(cell_id), val(0), emit: metadata

    script:
    """
    # Create daughter states so workflow can continue
    touch daughter_state_0.json
    touch daughter_state_1.json
    python ${params.project_root}/ecoli/experiments/ecoli_master_sim.py \
        -c $config \
        --sim_data_path $sim_data \
        --initial_state_file $initial_state \
        --daughter_outdir \$(pwd)
    """

    stub:
    """
    echo "$config $sim_data $initial_seed $generation" > daughter_state_0.json
    echo "$initial_state $sim_seed" > daughter_state_1.json
    """
}
