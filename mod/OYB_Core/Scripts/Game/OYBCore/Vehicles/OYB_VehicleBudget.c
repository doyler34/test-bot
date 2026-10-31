// Part of the vehicle limiter (see OYB_VehicleSettings.c).

// A vehicle's supply cost in the building menu, for the vehicle list. Prices
// set on the panel only change what depots charge: putting them on the
// building menu's cards broke every card's cost once a priced card was shown,
// so the building menu is left as the game has it.
class OYB_VehicleBudget
{
	// The game's own campaign cost for the entity itself, or -1 when it sets
	// none.
	static int OwnCampaignCost(SCR_EditableEntityUIInfo info)
	{
		if (!info)
			return -1;

		array<ref SCR_EntityBudgetValue> own = {};
		info.GetEntityBudgetCost(own);
		foreach (SCR_EntityBudgetValue budget : own)
		{
			if (budget.GetBudgetType() == EEditableEntityBudget.CAMPAIGN)
				return budget.GetBudgetValue();
		}
		return -1;
	}
}
