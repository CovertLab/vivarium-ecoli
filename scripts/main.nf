process parca {
    output:
    path "$params.outdir"
    
    script:
    """
    python /vivarium-ecoli/scripts/run_parca.py -o $params.outdir -c 11
    """

    stub:
    """
    echo $process.executor
    mkdir -p $params.outdir/kb
    touch $params.outdir/kb/simData.cPickle
    touch $params.outdir/kb/rawData.cPickle
    touch $params.outdir/kb/rawValidationData.cPickle
    touch $params.outdir/kb/validationData.cPickle
    touch $params.outdir/kb/metricsData.cPickle
    """
}

process variant {
    input:
    path outdir

    output:
    path "$params.variant_outdir"

    script:
    """
    """

    stub:
    """
    """
}

process ecoli_sim {
    input:
    path outdir
    
    output:
    stdout
    
    script:
    """
    python /vivarium-ecoli/ecoli/experiments/ecoli_master_sim.py --sim_data_path $outdir/kb/simData.cPickle
    """

    stub:
    """
    ls -la
    pwd
    """
}

workflow {
    parca()
    variant(parca.out)
    ecoli_sim(variant.out)
}
