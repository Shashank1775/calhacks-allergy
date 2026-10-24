"""kitchen: the allergen tracker's Python code, one folder per pipeline step.

    orders/        1  ticket notes -> typed constraints, questions, send       (team orders)
    profiling/     2  bin labels and prep sheets -> allergen profiles          (team orders)
    vision/        3  camera -> detections, tracks, entity IDs, frames         (team vision)
    entities/      4  in-memory entity store + 5 Hz database sync              (partner)
    interactions/  5  entity pairs -> features -> classified edges             (team interactions)
    lstm/          5b learned interaction classifier                           (partner)
    graph/         6  provenance graph, propagation, exact reachability        (partner)
    verdict/       7  plate at the pass -> PASS or HOLD, with the chain        (team verdict)
    gate/          8  servo, lights, buzzer, voice                             (team outputs)
    sim/           -  simulated kitchens, scenarios, Laya data and eval        (team sim)
    shared/        -  generated models, interfaces, clock, config, fakes       (team platform)

The SpacetimeDB module is in database/ (partner) and the screens are in web/ (team outputs).
Rules for agents: AGENTS.md. Data shapes between folders: contracts/.
"""
