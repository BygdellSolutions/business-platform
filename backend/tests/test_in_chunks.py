from app.core.query import IN_CHUNK, in_chunks


def test_values_come_in_chunks_of_at_most_the_size_in_their_order():
    values = list(range(10))

    assert list(in_chunks(values, 4)) == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9]]
    assert [x for chunk in in_chunks(values, 3) for x in chunk] == values  # nothing lost, nothing repeated, same order


def test_an_empty_input_gives_no_chunk_and_so_no_statement():
    assert list(in_chunks([])) == []
    assert list(in_chunks(iter(()))) == []


def test_a_generator_is_read_once_and_a_long_list_stays_below_the_limit():
    chunks = list(in_chunks(i for i in range(IN_CHUNK * 3 + 1)))

    assert [len(chunk) for chunk in chunks] == [IN_CHUNK, IN_CHUNK, IN_CHUNK, 1]
    assert IN_CHUNK * 76 < 5000  # an id costs about 76 bytes on the wire (its place in the statement plus its value): one chunk stays well below ~8 KB
