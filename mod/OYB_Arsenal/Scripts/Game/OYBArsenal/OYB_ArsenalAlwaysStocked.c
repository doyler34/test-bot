// Every infantry arsenal also offers its faction's medical gear and armour (vests
// and helmets), whatever the box was set up for, so a weapons-only box still has
// bandages and vests. Done before the arsenal builds its offer, on server and
// clients alike, so the replicated item types match. Arsenals with no infantry
// gear at all (vehicle pylons, mortars) are left alone.
modded class SCR_ArsenalComponent
{
	protected static const int OYB_INFANTRY_TYPES = SCR_EArsenalItemType.RIFLE | SCR_EArsenalItemType.PISTOL | SCR_EArsenalItemType.MACHINE_GUN | SCR_EArsenalItemType.SNIPER_RIFLE | SCR_EArsenalItemType.ROCKET_LAUNCHER | SCR_EArsenalItemType.LETHAL_THROWABLE | SCR_EArsenalItemType.NON_LETHAL_THROWABLE | SCR_EArsenalItemType.HEAL | SCR_EArsenalItemType.BACKPACK | SCR_EArsenalItemType.HEADWEAR | SCR_EArsenalItemType.TORSO | SCR_EArsenalItemType.VEST_AND_WAIST | SCR_EArsenalItemType.LEGS | SCR_EArsenalItemType.FOOTWEAR | SCR_EArsenalItemType.HANDWEAR | SCR_EArsenalItemType.EQUIPMENT | SCR_EArsenalItemType.WEAPON_ATTACHMENT | SCR_EArsenalItemType.EXPLOSIVES;
	protected static const int OYB_ALWAYS_TYPES = SCR_EArsenalItemType.HEAL | SCR_EArsenalItemType.VEST_AND_WAIST | SCR_EArsenalItemType.HEADWEAR;
	protected static const int OYB_ALWAYS_MODES = SCR_EArsenalItemMode.DEFAULT | SCR_EArsenalItemMode.CONSUMABLE;

	override protected void OnPostInit(IEntity owner)
	{
		if (m_eSupportedArsenalItemTypes & OYB_INFANTRY_TYPES)
		{
			m_eSupportedArsenalItemTypes |= OYB_ALWAYS_TYPES;
			m_eSupportedArsenalItemModes |= OYB_ALWAYS_MODES;
		}

		super.OnPostInit(owner);
	}
}
