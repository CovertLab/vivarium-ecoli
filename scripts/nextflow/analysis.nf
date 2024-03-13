process analysisSingle {
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

process analysisMultigen {
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

process analysisCohort {
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

process analysisVariant {
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
