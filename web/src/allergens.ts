/**
 * The nine allergens, read from contracts/allergen_units.json.
 * Nothing in web/ hardcodes the list.
 */
import allergen_units from "../../contracts/allergen_units.json";

export type Allergen = { id: string; name: string; default_severity: string };

export const ALLERGENS: Allergen[] = allergen_units.units;

/** Human name for an allergen ID; an unknown ID is shown as-is rather than hidden. */
export function allergen_name(id: string): string {
  return ALLERGENS.find((allergen) => allergen.id === id)?.name ?? id;
}
