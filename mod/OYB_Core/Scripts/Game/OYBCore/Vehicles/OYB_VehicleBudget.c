// Part of the vehicle limiter (see OYB_VehicleSettings.c). Named to compile
// before the other vehicle files, which use what it adds.

// A vehicle's price set on the panel, in the building menu. The game reads a
// placeable entity's supply cost ("campaign" budget) from its editable UI info
// everywhere: the menu card, the supply check before placing, the charge when
// the vehicle is registered, and the refund when it's removed. So the info
// learns which prefab it belongs to, and with a price set for that prefab
// (or for the vehicle a variant belongs to) its own campaign cost becomes the
// price; crew and other children's costs stay as the game has them.
//
// An info learns its prefab three ways: from the component source it's made
// from (the menu's supply checks), from the content browser that lists it
// (the menu cards), and from the vehicle it describes (the charge and the
// refund). Prefabs without a price, and every structure, are left alone.
modded class SCR_EditableEntityUIInfo
{
	protected ResourceName m_sOYB_Prefab;

	void OYB_SetPrefab(ResourceName prefab)
	{
		m_sOYB_Prefab = prefab;
	}

	bool OYB_HasPrefab()
	{
		return !m_sOYB_Prefab.IsEmpty();
	}

	override void InitFromSource(IEntityComponentSource componentSource)
	{
		super.InitFromSource(componentSource);

		if (componentSource && m_sOYB_Prefab.IsEmpty())
			m_sOYB_Prefab = SCR_BaseContainerTools.GetPrefabResourceName(componentSource);
	}

	// The game's own campaign cost for the entity itself, or -1 when it sets
	// none.
	int OYB_GetOwnCampaignCost()
	{
		array<ref SCR_EntityBudgetValue> own = {};
		super.GetEntityBudgetCost(own);
		return OYB_CampaignValue(own);
	}

	override bool GetEntityBudgetCost(out notnull array<ref SCR_EntityBudgetValue> outBudgets)
	{
		int change;
		bool priced = OYB_PriceChange(change);
		bool result = super.GetEntityBudgetCost(outBudgets);
		if (priced)
			OYB_ChangeCampaign(outBudgets, change);
		return result;
	}

	override void GetEntityAndChildrenBudgetCost(out notnull array<ref SCR_EntityBudgetValue> outBudgets)
	{
		int change;
		bool priced = OYB_PriceChange(change);
		super.GetEntityAndChildrenBudgetCost(outBudgets);
		if (priced)
			OYB_ChangeCampaign(outBudgets, change);
	}

	// How much the price differs from the game's own cost, when a price is set
	// and the entity has a cost of its own to replace.
	protected bool OYB_PriceChange(out int change)
	{
		int price;
		if (m_sOYB_Prefab.IsEmpty() || !OYB_VehicleSettings.Get().GetPrice(m_sOYB_Prefab, price))
			return false;

		int own = OYB_GetOwnCampaignCost();
		if (own < 0)
			return false;

		change = price - own;
		return true;
	}

	protected static int OYB_CampaignValue(notnull array<ref SCR_EntityBudgetValue> budgets)
	{
		foreach (SCR_EntityBudgetValue budget : budgets)
		{
			if (budget.GetBudgetType() == EEditableEntityBudget.CAMPAIGN)
				return budget.GetBudgetValue();
		}
		return -1;
	}

	// Replaces the campaign entry with a new one rather than changing it: the
	// entries in the list can be the info's own.
	protected static void OYB_ChangeCampaign(notnull array<ref SCR_EntityBudgetValue> budgets, int change)
	{
		foreach (int i, SCR_EntityBudgetValue budget : budgets)
		{
			if (budget.GetBudgetType() != EEditableEntityBudget.CAMPAIGN)
				continue;

			budgets[i] = new SCR_EntityBudgetValue(EEditableEntityBudget.CAMPAIGN, Math.Max(0, budget.GetBudgetValue() + change));
			return;
		}
	}
}

// The info a vehicle reads its costs from is its prefab's, so it learns that
// prefab here before the game reads the cost to charge or refund it.
modded class SCR_EditableEntityComponent
{
	override bool GetEntityBudgetCost(out notnull array<ref SCR_EntityBudgetValue> outBudgets, IEntity owner = null)
	{
		OYB_TagInfo(owner);
		return super.GetEntityBudgetCost(outBudgets, owner);
	}

	override bool GetEntityAndChildrenBudgetCost(out notnull array<ref SCR_EntityBudgetValue> outBudgets, IEntity owner = null)
	{
		OYB_TagInfo(owner);
		return super.GetEntityAndChildrenBudgetCost(outBudgets, owner);
	}

	protected void OYB_TagInfo(IEntity owner)
	{
		if (!owner)
			owner = GetOwner();
		if (!owner)
			return;

		SCR_EditableEntityUIInfo info = SCR_EditableEntityUIInfo.Cast(GetInfo(owner));
		if (info && !info.OYB_HasPrefab())
			info.OYB_SetPrefab(SCR_ResourceNameUtils.GetPrefabName(owner));
	}
}
