# kitchen/

All Python code, one folder per pipeline step. Open a folder's README to see what it does, its files, what goes in and out, and how it's tested.

| Step | Folder | What it does | Owner |
|---|---|---|---|
| 1 | [orders/](orders/) | Ticket notes become typed constraints, low-confidence fields become one-tap questions, and the sent ticket goes to the database | team orders |
| 2 | [profiling/](profiling/) | Bin labels and prep sheets become allergen profiles; the chef confirms unclear items | team orders |
| 3 | [vision/](vision/) | Camera frames become detections, tracks, entity IDs and recorded frames | team vision |
| 4 | [entities/](entities/) | Keeps every entity in memory on the Pi and syncs to the database at about 5 Hz | **partner** |
| 5 | [interactions/](interactions/) | Nearby entity pairs become features and clips, which become classified edges | **partner** |
| 5b | [lstm/](lstm/) | The learned interaction classifier | **partner** |
| 6 | [graph/](graph/) | The provenance graph: propagation, wash resets, time-forward paths, exact reachability | **partner** |
| 7 | [verdict/](verdict/) | A plate at the pass gets PASS or HOLD, with the contamination chain | team verdict |
| 8 | [gate/](gate/) | Servo arm, red/green light, buzzer, and voice ("Hold seven") | team outputs |
| — | [sim/](sim/) | Simulated kitchens, scenarios, and training clips for the partner's ConvLSTM | team sim |
| — | [shared/](shared/) | Models generated from `contracts/`, interfaces, clock, config, fakes | team platform |

Also: [`../database/`](../database/) (SpacetimeDB module, partner), [`../web/`](../web/) (screens, team outputs), [`../contracts/`](../contracts/) (data shapes between folders).

Every file starts with a docstring giving its owner, step, inputs and outputs, the safety rules that apply, and the tasks that build it. Agents never edit partner folders.
