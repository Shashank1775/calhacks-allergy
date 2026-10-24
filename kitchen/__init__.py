"""kitchen: the allergen tracker's Python code, one folder per pipeline step.

    orders/        ticket notes -> typed allergen data (Laya + one-tap questions); demo bins    (team orders)
    vision/        camera -> detections, tracks, entity IDs, change masks, recordings              (team vision)
    entities/      in-memory entity store + database sync at about 5 Hz                            (partner)
    interactions/  entity pairs -> features and clips -> classified edges                          (partner)
    lstm/          the learned interaction classifier                                              (partner)
    graph/         provenance graph, propagation, exact reachability                               (partner)
    verdict/       plate at the pass -> PASS or HOLD, with the chain                               (team verdict)
    gate/          servo, lights, buzzer, voice                                                    (team outputs)
    sim/           simulated kitchens: test scenarios and ConvLSTM training clips                  (team sim)
    shared/        generated models, interfaces, clock, config, fakes                              (team platform)

The SpacetimeDB module is in database/ (partner) and the screens are in web/ (team outputs).
Rules for agents: AGENTS.md. Data shapes between folders: contracts/.
"""
