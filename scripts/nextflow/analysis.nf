process analysis_parca {
    publishDir "${params.publish_dir}/parca"

    input:
    path config
    path sim_data
    path validation_data

    output:
    path 'plots/*'

    script:
    """
    python ${params.project_root}/scripts/analysis.py -c $config \
        --sim-data-path=$sim_data --validation-data-path=$validation_data \
        --parca -o \$(pwd)
    """

    stub:
    """
    mkdir plots
    touch plots/${sim_data.getBaseName()}
    touch plots/${validation_data.getBaseName()}
    """
}

process analysis_single {
    publishDir "${params.publish_dir}/analyses/${sim_data.getBaseName()}/${seed}/${generation}/${cell}"

    input:
    path config
    path sim_data
    path validation_data
    val seed
    val generation
    val cell
    path db

    output:
    path 'plots/*'

    script:
    """
    python ${params.project_root}/scripts/analysis.py -c $config \
        --sim-data-path=$sim_data --validation-data-path=$validation_data \
        --dbs $db --single -o \$(pwd)
    """

    stub:
    """
    mkdir plots
    touch plots/${sim_data.getBaseName()}
    touch plots/${validation_data.getBaseName()}
    """
}

process analysis_multigen {
    tag "$sim_data, $seed"
    publishDir "${params.publish_dir}/analyses/${sim_data.getBaseName()}/${seed}"

    input:
    path config
    path sim_data
    path validation_data
    val seed
    path dbs

    output:
    path 'plots/*'

    script:
    """
    python ${params.project_root}/scripts/analysis.py -c $config \
        --sim-data-path=$sim_data --validation-data-path=$validation_data \
        --dbs ${dbs.join(' ')} --multigen -o \$(pwd)
    """

    stub:
    """
    mkdir plots
    touch plots/${sim_data.getBaseName()}
    touch plots/${validation_data.getBaseName()}
    """
}

process analysis_cohort {
    tag "$sim_data"
    publishDir "${params.publish_dir}/analyses/${sim_data.getBaseName()}"

    input:
    path config
    path sim_data
    path validation_data
    path dbs

    output:
    path 'plots/*'

    script:
    """
    python ${params.project_root}/scripts/analysis.py -c $config \
        --sim-data-path=$sim_data --validation-data-path=$validation_data \
        --dbs ${dbs.join(' ')} --multigen -o \$(pwd)
    """

    stub:
    """
    mkdir plots
    touch plots/${sim_data.getBaseName()}
    touch plots/${validation_data.getBaseName()}
    """
}

process analysis_variant {
    publishDir "${params.publish_dir}/analyses"

    input:
    path config
    path sim_data
    path validation_data
    path dbs

    output:
    path 'plots/*'

    script:
    """
    python ${params.project_root}/scripts/analysis.py -c $config \
        --sim-data-path=$sim_data --validation-data-path=$validation_data \
        --dbs ${dbs.join(' ')} --multigen -o \$(pwd)
    """

    stub:
    """
    mkdir plots
    touch plots/${sim_data.getBaseName()}
    touch plots/${validation_data.getBaseName()}
    """
}
