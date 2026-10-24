use spacetimedb::{ReducerContext, Table, SpacetimeType};

#[derive(SpacetimeType, Clone)]
pub struct AllergenProb {
    allergen_id: String,
    p: f32,
}

#[spacetimedb::table(accessor = allergen, public)]
pub struct Allergen {
    #[primary_key]
    id: String,
    name: String,
    default_severity: String,
} 

#[spacetimedb::table(accessor = entity, public)]
pub struct Entity {
    #[primary_key] id: String,
    kind: String,            
    label: String,
    origin: String,          
    first_seen_t_ms: u64,
}

#[spacetimedb::table(accessor = entity_state, public,
    index(accessor = by_entity_time, btree(columns = [entity_id, t_ms])))]
pub struct EntityState {
    #[primary_key] #[auto_inc] state_id: u64,
    entity_id: String,
    version: u32,
    t_ms: u64,
    zone: String,
    allergens: Vec<AllergenProb>,
    unknown: bool,                    
    caused_by: Option<String>,        
}

#[spacetimedb::table(accessor = interaction, public)]
pub struct Interaction{
    #[primary_key]
    event_id: String,
    #[index(btree)]
    t_start_ms: u64,
    t_end_ms: u64,
    a_id: String,
    b_id: String,
    action: String,
    p: f32,
    source: String,
}

#[spacetimedb::table(accessor = ticket, public)]
pub struct Ticket {
    #[primary_key]
    id: String,
    note: String,
    forbidden: Vec<String>,
    pass_threshold: f32,
    revision: u32
}

#[spacetimedb::reducer(client_connected)]
pub fn identity_connected(_ctx: &ReducerContext) {
    // Called everytime a new client connects
}

#[spacetimedb::reducer(client_disconnected)]
pub fn identity_disconnected(_ctx: &ReducerContext) {
    // Called everytime a client disconnects
}

#[spacetimedb::reducer]
pub fn register_entity(ctx: &ReducerContext, id: String, kind: String, label: String, origin: String, time_seen: u64) {
    let entity = Entity {
        id,
        kind,
        label,
        origin,
        first_seen_t_ms: time_seen,
    };
    ctx.db.entity().insert(entity);
}

#[spacetimedb::reducer]
pub fn record_interaction(ctx: &ReducerContext, event_id: String, t_start_ms: u64, t_end_ms: u64, a_id: String, b_id: String, action: String, p: f32, source: String) {
    let inter = Interaction {
        event_id,
        t_start_ms,
        t_end_ms,
        a_id,
        b_id,
        action,
        p,
        source,
    };
    ctx.db.interaction().insert(inter);
}

#[spacetimedb::reducer]
pub fn move_entity(ctx: &ReducerContext, entity_id: String, version: u32, t_ms: u64, zone: String, allergens: Vec<AllergenProb>, unknown: bool, caused_by: Option<String>) {
    let e_state = EntityState {
        state_id: 0,
        entity_id,
        version,
        t_ms,
        zone,
        allergens,
        unknown,
        caused_by,
    };
    ctx.db.entity_state().insert(e_state);
}

#[spacetimedb::reducer]
pub fn put_ticket(ctx: &ReducerContext, id: String, note: String, forbidden: Vec<String>, pass_threshold: f32, revision: u32) {
    let tick: Ticket = Ticket {
        id,
        note,
        forbidden,
        pass_threshold,
        revision
    };
    ctx.db.ticket().insert(tick);
}



