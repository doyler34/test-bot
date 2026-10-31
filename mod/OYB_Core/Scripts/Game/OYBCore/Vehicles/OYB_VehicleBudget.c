// Part of the vehicle limiter (see OYB_VehicleSettings.c).

// A vehicle's supply cost in the building menu. The game's costs there are
// left alone: putting the price on the cards broke every card's cost once a
// priced card was shown. A price is charged on the server instead, on top of
// the game's own cost (see OYB_VehicleMenu.c), so the card shows the game's.
class OYB_VehicleBudget
{
	// The game's whole campaign cost, the entity's and its children's (crew).
	// Only for an info made for the call, as reading it adds the children's
	// costs into the info's own.
	static int CampaignCost(SCR_EditableEntityUIInfo info)
	{
		if (!info)
			return 0;

		int cost;
		array<ref SCR_EntityBudgetValue> all = {};
		info.GetEntityAndChildrenBudgetCost(all);
		foreach (SCR_EntityBudgetValue budget : all)
		{
			if (budget.GetBudgetType() == EEditableEntityBudget.CAMPAIGN)
				cost += budget.GetBudgetValue();
		}
		return cost;
	}

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
