process gcpBuildRuntimeImage {
    """
    
    """
}

process gcpBuildWcmImage {
    """
    
    """
}

process run_parca {
    // Run ParCa using parca_options from config JSON
    publishDir "${params.publishDir}/parca"

    input:
    path config

    output:
    path "kb"

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

process create_variants {
    // Parse variants in config JSON to generate variants
    publishDir "${params.publishDir}/variant_sim_data"

    input:
    path config
    path kb

    output:
    path "*.cPickle", emit: variant_sim_data
    path "metadata.json", emit: variant_metadata

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

IMPORTS

workflow {
    if( params.onGcp ) {
        gcp_build_image()
    }
    else if ( params.onSherlock ) {
        sherlock_build_image()
    }
    run_parca(params.config)
    run_parca.out.toList().set { kb }
    create_variants(params.config, kb)
        .variant_sim_data
        .flatten()
        .set { variant_ch }
WORKFLOW
}
