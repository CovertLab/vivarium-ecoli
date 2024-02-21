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

process analysis {
    publishDir "${params.publish_dir}/analyses/${sim_data.getBaseName()}/${seed}/${generation}/${cell}"

    input:
    path config
    path kb
    tuple path(sim_data), val(seed), val(generation), val(cell_id)
    val analysis_type

    output:
    path 'plots/*'

    script:
    """
    python ${params.project_root}/scripts/analysis.py -c $config \
        --sim-data-path=$sim_data \
        --validation-data-path=$kb/validationData.cPickle \
        --$analysis_type -o \$(pwd)
    """

    stub:
    """
    mkdir plots
    echo -e "$sim_data\n\n$seed\n\n\$generation\n\n\$cell_id\
        \n\n\$kb\n\n\$config\n\n\$analysis_type" > plots/test.txt
    """
}

IMPORTS

workflow {
    run_parca(params.config)
    run_parca.toList().set { kb }
    create_variants(params.config, kb)
        .variant_sim_data
        .flatten()
        .set { variant_ch }
    WORKFLOW
}
