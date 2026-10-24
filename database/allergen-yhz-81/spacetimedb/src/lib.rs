//! SpacetimeDB module for the kitchen allergen tracker: every table and reducer in contracts/state-tables.md.
//!
//! Owner: partner (written by an agent at the owner's request)
//! Reads: reducer calls from the Python StateClient (database/state_client.py); contracts/allergen_units.json at init
//! Emits: the tables allergen, zone, ticket, bin_profile, entity, entity_state, plate_binding, interaction, verdict
//! Rules: known-clean source: an entity is registered once and its first_seen_t_ms and origin never change;
//!        entity_state is append-only; late changes recheck everything: a ticket row is replaced only by its next
//!        revision; a plate binds once; every write is idempotent by primary key
//!
//! Column names follow the contracts, so the StateClient and the generated TypeScript bindings expose the contract
//! fields as they are; the one exception is entity_state.bbox for the contract's `box`, a Rust keyword. Probabilities
//! are f64, so a value written from Python reads back exactly. Auto-increment state_ids have gaps and don't order
//! rows: readers order entity_state by t_ms, then version.

use serde::Deserialize;
use spacetimedb::{ReducerContext, SpacetimeType, Table};

// ---------- shared column types ----------

/// One allergen and its probability: a row of an allergen profile, or a flagged allergen on a verdict.
#[derive(SpacetimeType, Clone, PartialEq)]
pub struct AllergenProbability {
    pub allergen_unit_id: String,
    pub probability: f64,
}

/// A normalized (x, y) point of a zone polygon.
#[derive(SpacetimeType, Clone, PartialEq)]
pub struct Point {
    pub x: f64,
    pub y: f64,
}

/// Which classifier produced an interaction edge, and which version of it.
#[derive(SpacetimeType, Clone, PartialEq)]
pub struct Classifier {
    pub name: String,
    pub version: String,
}

/// Where an interaction edge can be checked: its frames and an optional clip.
#[derive(SpacetimeType, Clone, PartialEq)]
pub struct Evidence {
    pub frame_start: Option<i64>,
    pub frame_end: Option<i64>,
    pub clip_uri: Option<String>,
}

/// One reason a verdict holds.
#[derive(SpacetimeType, Clone, PartialEq)]
pub struct Reason {
    pub code: String,
    pub allergen_unit_id: Option<String>,
    pub detail: Option<String>,
}

/// One hop of the contamination chain a verdict explains.
#[derive(SpacetimeType, Clone, PartialEq)]
pub struct ChainStep {
    pub step: u32,
    pub event_id: String,
    pub from_id: String,
    pub to_id: String,
    pub t_ms: u64,
    pub action: String,
    pub allergen_unit_id: String,
    pub step_confidence: f64,
}

/// The versions of every part that went into a verdict.
#[derive(SpacetimeType, Clone, PartialEq)]
pub struct Versions {
    pub policy: String,
    pub graph: String,
    pub classifier: Option<String>,
    pub laya: Option<String>,
}

// ---------- tables (contracts/state-tables.md) ----------

#[spacetimedb::table(accessor = allergen, public)]
pub struct Allergen {
    #[primary_key]
    id: String,
    name: String,
    default_severity: String,
}

#[spacetimedb::table(accessor = zone, public)]
pub struct Zone {
    #[primary_key]
    zone_id: String,
    #[index(btree)]
    kind: String,
    polygon: Vec<Point>,
}

#[spacetimedb::table(accessor = ticket, public)]
#[derive(Clone, PartialEq)]
pub struct Ticket {
    #[primary_key]
    ticket_id: String,
    revision: u32,
    number: u32,
    note: String,
    source: String,
    created_t_ms: u64,
    pass_threshold: f64,
    forbidden: Vec<String>,
}

#[spacetimedb::table(accessor = bin_profile, public)]
pub struct BinProfile {
    #[primary_key]
    entity_id: String,
    probabilities: Vec<AllergenProbability>,
    unknown: bool,
    source: String,
}

#[spacetimedb::table(accessor = entity, public)]
pub struct Entity {
    #[primary_key]
    entity_id: String,
    #[index(btree)]
    kind: String,
    label: String,
    origin: String,
    first_seen_t_ms: u64,
}

#[spacetimedb::table(accessor = entity_state, public,
    index(accessor = by_entity_time, btree(columns = [entity_id, t_ms])))]
pub struct EntityState {
    #[primary_key]
    #[auto_inc]
    state_id: u64,
    entity_id: String,
    version: u64,
    t_ms: u64,
    zone: Option<String>,
    bbox: Option<Vec<f64>>, // the contract's `box` ([x1, y1, x2, y2], normalized); `box` is a Rust keyword
    probabilities: Vec<AllergenProbability>,
    unknown: bool,
    caused_by: Option<String>,
}

#[spacetimedb::table(accessor = plate_binding, public)]
#[derive(Clone, PartialEq)]
pub struct PlateBinding {
    #[primary_key]
    plate_id: String,
    #[index(btree)]
    ticket_id: String,
    t_ms: u64,
}

#[spacetimedb::table(accessor = interaction, public)]
pub struct Interaction {
    #[primary_key]
    event_id: String,
    #[index(btree)]
    t_start_ms: u64,
    t_end_ms: u64,
    a_id: String,
    b_id: String,
    action: String,
    probability: f64,
    direction: String,
    classifier: Classifier,
    evidence: Option<Evidence>,
}

#[spacetimedb::table(accessor = verdict, public)]
pub struct Verdict {
    #[primary_key]
    verdict_id: String,
    #[index(btree)]
    ticket_id: String,
    ticket_revision: u32,
    #[index(btree)]
    plate_id: String,
    decided_t_ms: u64,
    trigger: String,
    decision: String,
    confidence: f64,
    pass_threshold: f64,
    reasons: Vec<Reason>,
    flagged: Vec<AllergenProbability>,
    chain: Vec<ChainStep>,
    versions: Versions,
}

// ---------- the allergen list ----------

/// contracts/allergen_units.json, compiled in so the allergen table is seeded from the one list everyone reads.
const ALLERGEN_UNITS_JSON: &str = include_str!("../../../../contracts/allergen_units.json");

#[derive(Deserialize)]
struct AllergenUnits {
    units: Vec<AllergenUnit>,
}

#[derive(Deserialize)]
struct AllergenUnit {
    id: String,
    name: String,
    default_severity: String,
}

/// Write every allergen unit into the allergen table. Units already there are left alone.
fn seed_allergens(ctx: &ReducerContext) -> Result<(), String> {
    let units: AllergenUnits =
        serde_json::from_str(ALLERGEN_UNITS_JSON).map_err(|error| format!("allergen_units.json: {error}"))?;
    for unit in units.units {
        if ctx.db.allergen().id().find(&unit.id).is_none() {
            ctx.db.allergen().insert(Allergen { id: unit.id, name: unit.name, default_severity: unit.default_severity });
        }
    }
    Ok(())
}

// ---------- lifecycle ----------

/// Runs when the module is first published (and when its data is cleared): seed the allergen table.
#[spacetimedb::reducer(init)]
pub fn init(ctx: &ReducerContext) -> Result<(), String> {
    seed_allergens(ctx)
}

#[spacetimedb::reducer(client_connected)]
pub fn identity_connected(_ctx: &ReducerContext) {}

#[spacetimedb::reducer(client_disconnected)]
pub fn identity_disconnected(_ctx: &ReducerContext) {}

/// Shift start: each shift begins with an empty database (contracts/state-tables.md). Clears every table except
/// the allergen list, which is reseeded.
#[spacetimedb::reducer]
pub fn start_shift(ctx: &ReducerContext) -> Result<(), String> {
    for row in ctx.db.zone().iter() {
        ctx.db.zone().delete(row);
    }
    for row in ctx.db.ticket().iter() {
        ctx.db.ticket().delete(row);
    }
    for row in ctx.db.bin_profile().iter() {
        ctx.db.bin_profile().delete(row);
    }
    for row in ctx.db.entity().iter() {
        ctx.db.entity().delete(row);
    }
    for row in ctx.db.entity_state().iter() {
        ctx.db.entity_state().delete(row);
    }
    for row in ctx.db.plate_binding().iter() {
        ctx.db.plate_binding().delete(row);
    }
    for row in ctx.db.interaction().iter() {
        ctx.db.interaction().delete(row);
    }
    for row in ctx.db.verdict().iter() {
        ctx.db.verdict().delete(row);
    }
    seed_allergens(ctx)
}

// ---------- writes: one writer team per table ----------

/// orders: insert a ticket, or replace it with its next revision (an amendment). Repeating the same write is a no-op.
///
/// Any other revision is refused: writing an older one would undo an amendment and judge the plate against
/// constraints the guest already changed (late changes recheck everything).
#[spacetimedb::reducer]
pub fn put_ticket(
    ctx: &ReducerContext,
    ticket_id: String,
    revision: u32,
    number: u32,
    note: String,
    source: String,
    created_t_ms: u64,
    pass_threshold: f64,
    forbidden: Vec<String>,
) -> Result<(), String> {
    let ticket = Ticket { ticket_id, revision, number, note, source, created_t_ms, pass_threshold, forbidden };
    match ctx.db.ticket().ticket_id().find(&ticket.ticket_id) {
        None => {
            ctx.db.ticket().insert(ticket);
        }
        Some(current) if current == ticket => {}
        Some(current) if ticket.revision == current.revision + 1 => {
            ctx.db.ticket().ticket_id().update(ticket);
        }
        Some(current) => {
            return Err(format!(
                "ticket {} is at revision {}; an amendment must be revision {}, got {}",
                current.ticket_id,
                current.revision,
                current.revision + 1,
                ticket.revision
            ));
        }
    }
    Ok(())
}

/// orders: insert or replace a bin's allergen profile.
#[spacetimedb::reducer]
pub fn put_bin_profile(
    ctx: &ReducerContext,
    entity_id: String,
    probabilities: Vec<AllergenProbability>,
    unknown: bool,
    source: String,
) -> Result<(), String> {
    let profile = BinProfile { entity_id, probabilities, unknown, source };
    if ctx.db.bin_profile().entity_id().find(&profile.entity_id).is_some() {
        ctx.db.bin_profile().entity_id().update(profile);
    } else {
        ctx.db.bin_profile().insert(profile);
    }
    Ok(())
}

/// vision: replace the whole calibration with `zones`.
#[spacetimedb::reducer]
pub fn set_zones(ctx: &ReducerContext, zones: Vec<ZoneInput>) -> Result<(), String> {
    for row in ctx.db.zone().iter() {
        ctx.db.zone().delete(row);
    }
    for zone in zones {
        ctx.db.zone().insert(Zone { zone_id: zone.zone_id, kind: zone.kind, polygon: zone.polygon });
    }
    Ok(())
}

/// One zone as set_zones receives it.
#[derive(SpacetimeType, Clone)]
pub struct ZoneInput {
    pub zone_id: String,
    pub kind: String,
    pub polygon: Vec<Point>,
}

/// vision ("start plate"): bind a plate to a ticket. A plate binds once: the same binding again is a no-op, and any
/// other is refused, since rebinding would judge the plate against another guest's ticket.
#[spacetimedb::reducer]
pub fn bind_plate(ctx: &ReducerContext, plate_id: String, ticket_id: String, t_ms: u64) -> Result<(), String> {
    let binding = PlateBinding { plate_id, ticket_id, t_ms };
    match ctx.db.plate_binding().plate_id().find(&binding.plate_id) {
        None => {
            ctx.db.plate_binding().insert(binding);
            Ok(())
        }
        Some(current) if current == binding => Ok(()),
        Some(current) => Err(format!("plate {} is already bound to {}", current.plate_id, current.ticket_id)),
    }
}

/// entity store: register an entity once. Registering it again is a no-op, whatever the second call says, so a
/// later write can never turn an unknown-history tool into a clean one (known-clean source).
#[spacetimedb::reducer]
pub fn register_entity(
    ctx: &ReducerContext,
    entity_id: String,
    kind: String,
    label: String,
    origin: String,
    first_seen_t_ms: u64,
) -> Result<(), String> {
    if ctx.db.entity().entity_id().find(&entity_id).is_none() {
        ctx.db.entity().insert(Entity { entity_id, kind, label, origin, first_seen_t_ms });
    }
    Ok(())
}

/// entity store (zone, box) and graph (allergens): append a state row for a registered entity. Rows are never
/// edited or removed (history is append-only).
#[spacetimedb::reducer]
pub fn move_entity(
    ctx: &ReducerContext,
    entity_id: String,
    version: u64,
    t_ms: u64,
    zone: Option<String>,
    bbox: Option<Vec<f64>>,
    probabilities: Vec<AllergenProbability>,
    unknown: bool,
    caused_by: Option<String>,
) -> Result<(), String> {
    if ctx.db.entity().entity_id().find(&entity_id).is_none() {
        return Err(format!("move_entity for unregistered entity {entity_id}"));
    }
    ctx.db.entity_state().insert(EntityState {
        state_id: 0, // auto_inc assigns it
        entity_id,
        version,
        t_ms,
        zone,
        bbox,
        probabilities,
        unknown,
        caused_by,
    });
    Ok(())
}

/// interactions: insert an interaction edge. A duplicate event_id is a no-op.
#[spacetimedb::reducer]
pub fn record_interaction(
    ctx: &ReducerContext,
    event_id: String,
    t_start_ms: u64,
    t_end_ms: u64,
    a_id: String,
    b_id: String,
    action: String,
    probability: f64,
    direction: String,
    classifier: Classifier,
    evidence: Option<Evidence>,
) -> Result<(), String> {
    if ctx.db.interaction().event_id().find(&event_id).is_none() {
        ctx.db.interaction().insert(Interaction {
            event_id,
            t_start_ms,
            t_end_ms,
            a_id,
            b_id,
            action,
            probability,
            direction,
            classifier,
            evidence,
        });
    }
    Ok(())
}

/// verdict: insert a verdict, chain included. A duplicate verdict_id is a no-op.
#[spacetimedb::reducer]
pub fn record_verdict(
    ctx: &ReducerContext,
    verdict_id: String,
    ticket_id: String,
    ticket_revision: u32,
    plate_id: String,
    decided_t_ms: u64,
    trigger: String,
    decision: String,
    confidence: f64,
    pass_threshold: f64,
    reasons: Vec<Reason>,
    flagged: Vec<AllergenProbability>,
    chain: Vec<ChainStep>,
    versions: Versions,
) -> Result<(), String> {
    if ctx.db.verdict().verdict_id().find(&verdict_id).is_none() {
        ctx.db.verdict().insert(Verdict {
            verdict_id,
            ticket_id,
            ticket_revision,
            plate_id,
            decided_t_ms,
            trigger,
            decision,
            confidence,
            pass_threshold,
            reasons,
            flagged,
            chain,
            versions,
        });
    }
    Ok(())
}
