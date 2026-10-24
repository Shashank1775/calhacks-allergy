# kitchen/

All Python code, one folder per pipeline step. Open a folder's README to see what it does, its files, what goes in and out, and how it's tested.

| Folder | What it does | Owner |
|---|---|---|
| [orders/](orders/) | Ticket notes and bin labels become typed allergen data, through Laya and one-tap questions | team orders |
| [vision/](vision/) | Camera frames become tracked, identified entities, change masks and recordings | team vision |
| [entities/](entities/) | Keeps every entity in memory on the Pi and syncs to the database at about 5 Hz | **partner** |
| [interactions/](interactions/) | Nearby entity pairs become features and clips, which become classified edges | **partner** |
| [lstm/](lstm/) | The learned interaction classifier | **partner** |
| [graph/](graph/) | The provenance graph: propagation, wash resets, time-forward paths, exact reachability | **partner** |
| [verdict/](verdict/) | A plate at the pass gets PASS or HOLD, with the contamination chain | team verdict |
| [gate/](gate/) | Servo arm, red/green light, buzzer, and voice ("Hold seven") | team outputs |
| [sim/](sim/) | Simulated kitchens: test scenarios and ConvLSTM training clips | team sim |
| [shared/](shared/) | Models generated from `contracts/`, interfaces, clock, config, fakes | team platform |

Also: [`../database/`](../database/) (SpacetimeDB module, partner), [`../web/`](../web/) (screens, team outputs), [`../contracts/`](../contracts/) (data shapes between folders).

Every file starts with a docstring giving its owner, step, inputs and outputs, and the safety rules that apply. Agents never edit partner folders.
