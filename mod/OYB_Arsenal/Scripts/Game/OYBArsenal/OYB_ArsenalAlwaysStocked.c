// Every infantry arsenal is a full arsenal: whatever the box was set up for
// (clothing, equipment, weapons...), it offers all of its faction's infantry
// gear. Done before the arsenal builds its offer, on server and clients alike,
// so the replicated item types match. Arsenals with no infantry gear at all
// (vehicle pylons, mortars) are left alone.
modded class SCR_ArsenalComponent
{
	protected static const int OYB_INFANTRY_TYPES = SCR_EArsenalItemType.RIFLE | SCR_EArsenalItemType.PISTOL | SCR_EArsenalItemType.MACHINE_GUN | SCR_EArsenalItemType.SNIPER_RIFLE | SCR_EArsenalItemType.ROCKET_LAUNCHER | SCR_EArsenalItemType.LETHAL_THROWABLE | SCR_EArsenalItemType.NON_LETHAL_THROWABLE | SCR_EArsenalItemType.HEAL | SCR_EArsenalItemType.BACKPACK | SCR_EArsenalItemType.HEADWEAR | SCR_EArsenalItemType.TORSO | SCR_EArsenalItemType.VEST_AND_WAIST | SCR_EArsenalItemType.LEGS | SCR_EArsenalItemType.FOOTWEAR | SCR_EArsenalItemType.HANDWEAR | SCR_EArsenalItemType.EQUIPMENT | SCR_EArsenalItemType.WEAPON_ATTACHMENT | SCR_EArsenalItemType.EXPLOSIVES;
	protected static const int OYB_ALWAYS_TYPES = OYB_INFANTRY_TYPES | SCR_EArsenalItemType.RADIO_BACKPACK;
	protected static const int OYB_ALWAYS_MODES = SCR_EArsenalItemMode.DEFAULT | SCR_EArsenalItemMode.WEAPON | SCR_EArsenalItemMode.WEAPON_VARIANTS | SCR_EArsenalItemMode.AMMUNITION | SCR_EArsenalItemMode.CONSUMABLE | SCR_EArsenalItemMode.ATTACHMENT;

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
