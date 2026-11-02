// Part of the vehicle limiter (see OYB_VehicleSettings.c).

// A vehicle's supply cost in the building menu, for the panel's vehicle list.
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

