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

IMPORTS

workflow {
    run_parca(params.config)
    create_variants(params.config, run_parca.out)
        .variant_sim_data
        .flatten()
        .set { variant_ch }
    WORKFLOW
}
