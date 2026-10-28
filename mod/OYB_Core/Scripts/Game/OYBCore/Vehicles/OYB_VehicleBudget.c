// Part of the vehicle limiter (see OYB_VehicleSettings.c).

// A vehicle's price set on the panel, in the building menu. The game reads a
// placeable entity's supply cost ("campaign" budget) from its editable UI info:
// the building budget component for the menu card and the supply check before
// placing, and the vehicle's editable entity component for the charge when it's
// registered and the refund when it's removed. With a price set for the prefab
// (or for the vehicle a variant belongs to), its own campaign cost there becomes
// the price; crew and other children's costs stay as the game has them.
// Prefabs without a price, and every structure, are left alone.
//
// The UI info itself isn't modded (the game's own code reads its private
// fields), so the menu cards' infos are matched to their prefabs here.
class OYB_VehicleBudget
{
	protected static ref map<SCR_EditableEntityUIInfo, ResourceName> s_mCardPrefabs = new map<SCR_EditableEntityUIInfo, ResourceName>();

	// The content browser's cards, each with the prefab it shows.
	static void NoteCards(notnull array<ref SCR_EditableEntityUIInfo> infos, notnull SCR_ContentBrowserEditorComponent browser)
	{
		s_mCardPrefabs.Clear();
		foreach (int index, SCR_EditableEntityUIInfo info : infos)
		{
			if (info)
				s_mCardPrefabs.Set(info, browser.GetResourceNamePrefabID(index));
		}
	}

	static ResourceName CardPrefab(SCR_EditableEntityUIInfo info)
	{
		ResourceName prefab;
		if (info)
			s_mCardPrefabs.Find(info, prefab);
		return prefab;
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

	// Changes the campaign cost in the list by the difference between the price
	// and the game's own cost. The entry is replaced rather than changed, as it
	// can be the info's own.
	static void ApplyPrice(ResourceName prefab, SCR_EditableEntityUIInfo info, notnull array<ref SCR_EntityBudgetValue> budgets, string where = "")
	{
		int price;
		if (prefab.IsEmpty() || !OYB_VehicleSettings.Get().GetPrice(prefab, price))
			return;

		int own = OwnCampaignCost(info);
		string before = Describe(budgets);
		if (own >= 0)
		{
			foreach (int i, SCR_EntityBudgetValue budget : budgets)
			{
				if (budget.GetBudgetType() != EEditableEntityBudget.CAMPAIGN)
					continue;

				budgets[i] = new SCR_EntityBudgetValue(EEditableEntityBudget.CAMPAIGN, Math.Max(0, budget.GetBudgetValue() + price - own));
				break;
			}
		}

		// Once per vehicle and place, to see what a price does to the costs.
		string key = where + prefab;
		if (s_aLogged.Contains(key))
			return;
		s_aLogged.Insert(key);
		PrintFormat("OYB_CORE price %1 (%2): price %3, own cost %4, costs before %5, after %6", FilePath.StripPath(prefab.GetPath()), where, price, own, before, Describe(budgets));
	}

	protected static ref array<string> s_aLogged = {};

	protected static string Describe(notnull array<ref SCR_EntityBudgetValue> budgets)
	{
		string text = "[";
		foreach (int i, SCR_EntityBudgetValue budget : budgets)
		{
			if (i > 0)
				text += ", ";
			if (budget)
				text += typename.EnumToString(EEditableEntityBudget, budget.GetBudgetType()) + " " + budget.GetBudgetValue();
			else
				text += "null";
		}
		return text + "]";
	}
}

// The menu card and the supply checks before placing.
modded class SCR_CampaignBuildingBudgetEditorComponent
{
	override bool GetEntityPreviewBudgetCosts(SCR_EditableEntityUIInfo entityUIInfo, out notnull array<ref SCR_EntityBudgetValue> budgetCosts)
	{
		bool result = super.GetEntityPreviewBudgetCosts(entityUIInfo, budgetCosts);
		OYB_VehicleBudget.ApplyPrice(OYB_VehicleBudget.CardPrefab(entityUIInfo), entityUIInfo, budgetCosts, "card");
		return result;
	}

	override bool GetEntitySourcePreviewBudgetCosts(IEntityComponentSource editableEntitySource, out notnull array<ref SCR_EntityBudgetValue> budgetCosts)
	{
		bool result = super.GetEntitySourcePreviewBudgetCosts(editableEntitySource, budgetCosts);
		OYB_ApplySourcePrice(editableEntitySource, budgetCosts);
		return result;
	}

	override void GetBudgetCostsDontDiscardCampaignBudget(IEntityComponentSource editableEntitySource, out notnull array<ref SCR_EntityBudgetValue> budgetCosts)
	{
		super.GetBudgetCostsDontDiscardCampaignBudget(editableEntitySource, budgetCosts);
		OYB_ApplySourcePrice(editableEntitySource, budgetCosts);
	}

	protected void OYB_ApplySourcePrice(IEntityComponentSource editableEntitySource, notnull array<ref SCR_EntityBudgetValue> budgetCosts)
	{
		if (!editableEntitySource)
			return;

		ResourceName prefab = SCR_BaseContainerTools.GetPrefabResourceName(editableEntitySource);
		OYB_VehicleBudget.ApplyPrice(prefab, SCR_EditableEntityComponentClass.GetInfo(editableEntitySource), budgetCosts, "check");
	}
}

// The charge when a placed vehicle is registered, and the refund when it's
// removed, read from the vehicle itself.
modded class SCR_EditableEntityComponent
{
	override bool GetEntityBudgetCost(out notnull array<ref SCR_EntityBudgetValue> outBudgets, IEntity owner = null)
	{
		bool result = super.GetEntityBudgetCost(outBudgets, owner);
		OYB_ApplyPrice(owner, outBudgets);
		return result;
	}

	override bool GetEntityAndChildrenBudgetCost(out notnull array<ref SCR_EntityBudgetValue> outBudgets, IEntity owner = null)
	{
		bool result = super.GetEntityAndChildrenBudgetCost(outBudgets, owner);
		OYB_ApplyPrice(owner, outBudgets);
		return result;
	}

	protected void OYB_ApplyPrice(IEntity owner, notnull array<ref SCR_EntityBudgetValue> budgets)
	{
		if (!owner)
			owner = GetOwner();
		if (!owner)
			return;

		OYB_VehicleBudget.ApplyPrice(SCR_ResourceNameUtils.GetPrefabName(owner), SCR_EditableEntityUIInfo.Cast(GetInfo(owner)), budgets, "charge");
	}
}
