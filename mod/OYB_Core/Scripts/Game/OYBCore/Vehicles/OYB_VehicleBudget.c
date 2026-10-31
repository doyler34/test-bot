// Part of the vehicle limiter (see OYB_VehicleSettings.c).

// A vehicle's supply cost in the building menu. The game's costs there are
// left alone: changing them where the game reads them broke every card's cost
// once a priced card was shown. A price is charged on the server on top of the
// game's own cost, and only the card's text shows it (see OYB_VehicleMenu.c).
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

// A limited run of log lines about the building menu's card costs, started
// again each time the settings change.
class OYB_CardLog
{
	protected static const int LINES = 600;
	protected static int s_iLeft = LINES;

	static void Restart(string why)
	{
		s_iLeft = LINES;
		PrintFormat("OYB_CORE cards: %1; logging the next %2 card costs", why, LINES);
	}

	static bool Take()
	{
		if (s_iLeft <= 0)
			return false;

		s_iLeft--;
		return true;
	}

	static string One(SCR_EntityBudgetValue budget)
	{
		if (!budget)
			return "nothing";
		return typename.EnumToString(EEditableEntityBudget, budget.GetBudgetType()) + " " + budget.GetBudgetValue();
	}

	static string All(notnull array<ref SCR_EntityBudgetValue> budgets)
	{
		string text = "[";
		foreach (int i, SCR_EntityBudgetValue budget : budgets)
		{
			if (i > 0)
				text += ", ";
			text += One(budget);
		}
		return text + "]";
	}
}
