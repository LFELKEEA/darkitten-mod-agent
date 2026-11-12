package {{package}};

import net.minecraft.block.AbstractBlock;
import net.minecraft.block.Block;
import net.minecraft.item.BlockItem;
import net.minecraft.item.Item;
import net.minecraft.registry.Registries;
import net.minecraft.registry.Registry;
import net.minecraft.util.Identifier;

/**
 * 本模组的注册中心：集中注册方块、物品，并在主类的 onInitialize 里调用。
 * 示例方块/物品会在进游戏后可用 /give 或 /setblock 直接拿到。
 */
public final class ModRegistries {
    public static final Block EXAMPLE_BLOCK =
            new Block(AbstractBlock.Settings.create().strength(2.0f));
    public static final Item EXAMPLE_ITEM =
            new Item(new Item.Settings());
    public static final Item EXAMPLE_BLOCK_ITEM =
            new BlockItem(EXAMPLE_BLOCK, new Item.Settings());

    public static void register() {
        Registry.register(Registries.BLOCK,
                new Identifier({{class_name}}.MOD_ID, "example_block"), EXAMPLE_BLOCK);
        Registry.register(Registries.ITEM,
                new Identifier({{class_name}}.MOD_ID, "example_block"), EXAMPLE_BLOCK_ITEM);
        Registry.register(Registries.ITEM,
                new Identifier({{class_name}}.MOD_ID, "example_item"), EXAMPLE_ITEM);
    }

    private ModRegistries() {
    }
}
