package {{package}};

import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.Item;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.state.BlockBehaviour;
import net.minecraftforge.eventbus.api.IEventBus;
import net.minecraftforge.registries.DeferredRegister;
import net.minecraftforge.registries.ForgeRegistries;
import net.minecraftforge.registries.RegistryObject;

/**
 * 本模组的注册中心：集中注册方块、物品，并在主类里挂到 Mod 事件总线。
 * 示例方块/物品会在进游戏后可用 /give 或 /setblock 直接拿到。
 */
public final class ModRegistries {
    public static final DeferredRegister<Block> BLOCKS =
            DeferredRegister.create(ForgeRegistries.BLOCKS, ModMain.MODID);
    public static final DeferredRegister<Item> ITEMS =
            DeferredRegister.create(ForgeRegistries.ITEMS, ModMain.MODID);

    public static final RegistryObject<Block> EXAMPLE_BLOCK =
            BLOCKS.register("example_block",
                    () -> new Block(BlockBehaviour.Properties.of().strength(2.0f)));
    public static final RegistryObject<Item> EXAMPLE_ITEM =
            ITEMS.register("example_item", () -> new Item(new Item.Properties()));
    public static final RegistryObject<Item> EXAMPLE_BLOCK_ITEM =
            ITEMS.register("example_block",
                    () -> new BlockItem(EXAMPLE_BLOCK.get(), new Item.Properties()));

    public static void register(IEventBus modEventBus) {
        BLOCKS.register(modEventBus);
        ITEMS.register(modEventBus);
    }

    private ModRegistries() {
    }
}
