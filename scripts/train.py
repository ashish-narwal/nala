import argparse
import os
import tempfile
from collections import Counter
from nala.preprocessing.definers import ExclusiveNLDefiner
from nala.utils.corpora import get_corpus
from nalaf.preprocessing.labelers import BIEOLabeler, BIOLabeler, TmVarLabeler
from nala.utils import get_prepare_pipeline_for_best_model
from nalaf.learning.crfsuite import PyCRFSuite
from nalaf.learning.evaluators import MentionLevelEvaluator
from nala.learning.taggers import NalaSingleModelTagger, NalaTagger
from nalaf.utils.writers import TagTogFormat
from nala.bootstrapping.document_filters import HighRecallRegexClassifier

if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='Print corpora stats')

    group1 = parser.add_mutually_exclusive_group(required=True)

    group1.add_argument('--training_corpus',
        help='Name of the corpus to train on. Examples: IDP4+, nala_training, IDP4+_training, nala_training_5')
    group1.add_argument('--test_corpus',
        help='Name of the corpus to test on')

    parser.add_argument('--cv_n', required = False,
        help='if given, cross validation (instead of stratification) is used for validating the training. \
             In this case you must also set `cv_fold` and only that fold number will be run')
    parser.add_argument('--cv_fold', required = False,
        help='fold number to train and validate if cross validation is activated')

    parser.add_argument('--output_folder', required = False,
        help='Folder where the training model is written to. Otherwise a tmp folder is used')
    parser.add_argument('--model_name_suffix', required = False,
        help='Optional suffix to add to the generated model name in training mode'),
    parser.add_argument('--write_anndoc', required = False, action='store_true',
        help='Write anndoc of predicted test_corpus'),
    parser.add_argument('--model_path_1', required = False,
        help='Path of the first model binary file if evaluation is performed')
    parser.add_argument('--model_path_2', required = False,
        help='Path of the second model binary file if evaluation is performed with two models')

    parser.add_argument('--labeler', required = False, default = "BIEO", choices=["BIEO", "BIO", "11labels"],
        help='Labeler to use for training')
    parser.add_argument('--delete_subclasses', required = False, default = "",
        help='Comma-separated subclasses to delete. Example: "2,3"')

    parser.add_argument('--pruner', required=False, default="parts", choices=["parts", "sentences"])
    parser.add_argument('--ps_ST', required=False, default=False, action='store_true')
    parser.add_argument('--ps_NL', required=False, default=False, action='store_true')
    parser.add_argument('--ps_random', required=False, default=0.0, type=float)

    parser.add_argument('--elastic_net', action='store_true',
        help='Use elastic net regularization')

    parser.add_argument('--word_embeddings', action='store_true',
        help='Use word embeddings features')
    parser.add_argument('--we_additive', type=int, default = 2)
    parser.add_argument('--we_multiplicative', type=int, default = 3)

    args = parser.parse_args()

    delete_subclasses = []
    for c in args.delete_subclasses.split(","):
        c.strip()
        if c:
            delete_subclasses.append(int(c))

    args.delete_subclasses = delete_subclasses

    if not args.output_folder:
        args.output_folder = tempfile.mkdtemp()

    str_delete_subclasses = "None" if not args.delete_subclasses else str(args.delete_subclasses).strip('[]').replace(' ','')

    if args.labeler == "BIEO":
        labeler = BIEOLabeler()
    elif args.labeler == "BIO":
        labeler = BIOLabeler()
    elif args.labeler == "11labels":
        labeler = TmVarLabeler()

    if args.word_embeddings:
        args.we_params = {
            'additive': args.we_additive,
            'multiplicative': args.we_multiplicative
        }
    else:
        args.we_params = None

    if args.elastic_net:
        args.crf_train_params = {
        'c1': 1.0, # coefficient for L1 penalty
        'c2': 1e-3, # coefficient for L2 penalty
        }
    else:
        args.crf_train_params = None

    args.do_train = False if args.model_path_1 else True
    if args.cv_n:
        assert args.cv_fold is not None, "You must set both cv_n AND cv_n"
    args.validation = "cross-validation" if args.cv_n else "stratified"

    #------------------------------------------------------------------------------

    args.model_name = "{}_{}_del_{}".format(args.training_corpus, args.labeler, str_delete_subclasses)
    if args.validation == "cross-validation":
        args.model_name += "_cvfold_" + str(args.cv_fold)
    if args.model_name_suffix:
        args.model_name += "_" + str(args.model_name_suffix)

    #------------------------------------------------------------------------------

    def print_run_args():
        for key, value in sorted((vars(args)).items()):
            print("\t{} = {}".format(key, value))
        print()

    print("Running arguments: ")
    print_run_args()

    #------------------------------------------------------------------------------

    features_pipeline = get_prepare_pipeline_for_best_model(args.we_params)

    #------------------------------------------------------------------------------

    def stats(dataset, name):
        print('\n\t{} size: {}'.format(name, len(dataset)))
        print('\tsubclass distribution: {}'.format(Counter(ann.subclass for ann in dataset.annotations())))
        print('\tnum sentences: {}\n'.format(sum(1 for x in dataset.sentences())))

    def train(train_set):

        ExclusiveNLDefiner().define(train_set)
        train_set.delete_subclass_annotations(args.delete_subclasses)
        features_pipeline.execute(train_set)
        labeler.label(train_set)
        if args.pruner == "parts":
            train_set.prune_empty_parts()
        else:
            try:
                f = HighRecallRegexClassifier(ST=args.ps_ST, NL=args.ps_NL)
            except AssertionError:
                f = (lambda _: False)
            train_set.prune_filtered_sentences(filterin=f, percent_to_keep=args.ps_random)

        stats(train_set, "training")

        crf = PyCRFSuite()

        model_path = os.path.join(args.output_folder, args.model_name + ".bin")
        crf.train(train_set, model_path, args.crf_train_params)

        return model_path

    if args.training_corpus:
        train_set = get_corpus(args.training_corpus)
        if args.validation == "cross-validation":
            train_set, test_set = train_set.fold_nr_split(int(args.cv_n), int(args.cv_fold))
        else:
            ExclusiveNLDefiner().define(train_set)
            train_set, test_set = train_set.stratified_split()
    else:
        train_set = test_set = None

    if args.do_train:
        args.model_path_1 = train(train_set)

    #------------------------------------------------------------------------------

    def test(tagger, test_set):
        tagger.tag(test_set)

        ExclusiveNLDefiner().define(test_set)

        print("\n{}".format(args.model_name))
        if train_set:
            stats(train_set, "training")
        stats(test_set, "test")
        print_run_args()

        exact = MentionLevelEvaluator(strictness='exact', subclass_analysis=True).evaluate(test_set)
        overlapping = MentionLevelEvaluator(strictness='overlapping', subclass_analysis=True).evaluate(test_set)

        for e in exact:
            print(e)
        print()
        for e in overlapping:
            print(e)
        print()

    assert(args.model_path_1 is not None)

    if args.model_path_2:
        tagger = NalaTagger(st_model = args.model_path_1, all3_model = args.model_path_2, features_pipeline = features_pipeline)
    else:
        tagger = NalaSingleModelTagger(bin_model = args.model_path_1, features_pipeline = features_pipeline)

    if test_set is None:
        test_set = get_corpus(args.test_corpus)

    test(tagger, test_set)

    if args.do_train:
        print("\nThe model is saved to: {}\n".format(args.model_path_1))

    if args.write_anndoc:
        outdir = os.path.join(args.output_folder, args.model_name)
        os.mkdir(outdir)
        print("\nThe predicted test data is saved to: {}\n".format(outdir))
        TagTogFormat(test_set, use_predicted=True, to_save_to=outdir).export(0)